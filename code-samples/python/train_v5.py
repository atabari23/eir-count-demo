"""Larger-backbone pill counter for S22 / iPhone 13 class phones and newer.

ImageNet-pretrained timm backbone (default EfficientNetV2-S) with a bilinear FPN to stride 4 and the same
two-channel head as the v4 model (centre heatmap, tray mask), trained at a 1024 px letterbox. Labels,
sampling (group-balanced + error-driven + region records), losses, matching (10 px at 640 scaled to the
input size) and the two-pass + saddle-merge evaluation are the same as train_collection.py so results are
directly comparable with the ledger in reports/scale-normalization-probe-20260917.json. Test groups are
never evaluated. Synthetic data is not used here.
"""
import argparse,hashlib,json,math,random,time,zipfile
from pathlib import Path
import numpy as np,torch,timm
from torch import nn
import torch.nn.functional as F
from PIL import Image,ImageDraw
from train_v2 import prepare_v2,region_loss
from train_pilot import heatmap,focal,match
from train_collection import require_label_audits,load_view_boxes,crop_view,training_batch
import postprocess,two_pass as tp
from postprocess import decode_clean

try:
    from scipy import ndimage
    def largest_component_fast(mask):
        """Same result as postprocess.largest_component (largest 8-connected region), much faster at 256x256."""
        labels,n=ndimage.label(np.asarray(mask).astype(bool),structure=np.ones((3,3),int))
        if n==0:return np.zeros(np.asarray(mask).shape,bool)
        sizes=np.bincount(labels.ravel());sizes[0]=0;return labels==int(sizes.argmax())
    postprocess.largest_component=largest_component_fast;tp.largest_component=largest_component_fast;FAST_COMPONENTS=True
except ImportError:FAST_COMPONENTS=False

def conv3x3(channels,separable):
    """3x3 block for the stride-4 head: dense, or depthwise + pointwise (about 9x fewer FLOPs) for phone CPUs."""
    if not separable:return nn.Conv2d(channels,channels,3,padding=1)
    return nn.Sequential(nn.Conv2d(channels,channels,3,padding=1,groups=channels),nn.Conv2d(channels,channels,1))

class Counter(nn.Module):
    """timm features (strides 4-32) -> bilinear FPN -> 3x3 head -> 2 logits per stride-4 cell (centre, tray).
    offset=True (round05) adds a centre-offset branch on the FPN features (3x3 as the head, SiLU, 1x1 -> 2, last layer
    zero-initialised); the output then has 4 channels (centre, tray, offset x, offset y; offsets in stride-4 cells =
    OFFSET_SCALE * value) and outputs[:, :2] is computed exactly as without the branch. The default (offset=False) builds
    and runs exactly the original 2-channel model with the original state-dict keys."""
    def __init__(self,backbone,channels=128,pretrained=True,head='dense',offset=False):
        super().__init__()
        self.body=timm.create_model(backbone,pretrained=pretrained,features_only=True)
        info=self.body.feature_info;self.keep=[i for i,r in enumerate(info.reduction()) if r in (4,8,16,32)]
        assert [info.reduction()[i] for i in self.keep]==[4,8,16,32],info.reduction()
        chans=[info.channels()[i] for i in self.keep];separable=head=='separable'
        self.lateral=nn.ModuleList([nn.Conv2d(c,channels,1) for c in chans])
        self.smooth=nn.Sequential(conv3x3(channels,separable),nn.BatchNorm2d(channels),nn.SiLU())
        self.head=nn.Sequential(conv3x3(channels,separable),nn.SiLU(),nn.Conv2d(channels,2,1))
        nn.init.constant_(self.head[-1].bias,-2.19)
        with torch.no_grad():self.head[-1].bias[1].zero_()
        self.offset=None
        if offset:
            self.offset=nn.Sequential(conv3x3(channels,separable),nn.SiLU(),nn.Conv2d(channels,2,1));nn.init.zeros_(self.offset[-1].weight);nn.init.zeros_(self.offset[-1].bias)
    def forward(self,x):
        feats=self.body(x);ys=[feats[i] for i in self.keep];y=self.lateral[3](ys[3])
        for i in (2,1,0):y=F.interpolate(y,size=ys[i].shape[-2:],mode='bilinear',align_corners=False)+self.lateral[i](ys[i])
        if self.offset is None:return self.head(self.smooth(y))
        z=self.smooth(y)
        return torch.cat([self.head(z),self.offset(z)],1)

OFFSET_SCALE=8.

def load_counter(path,device='cpu',offset=None):
    """Build a Counter from a checkpoint's config and load it. offset=None follows the checkpoint (4-channel if it has
    offset weights); offset=False drops the offset head (the 2-channel phone model: outputs identical to outputs[:, :2]);
    offset=True on a 2-channel checkpoint adds the offset branch (only offset.* keys may be missing): its 3x3 starts as a
    copy of the centre head's 3x3 (warm start) and its last 1x1 at zero, so the offsets start at exactly 0."""
    payload=torch.load(path,map_location='cpu',weights_only=False);cfg=payload['config'];state=payload['model']
    has=any(k.startswith('offset.') for k in state);offset=has if offset is None else offset
    if not offset:state={k:v for k,v in state.items() if not k.startswith('offset.')}
    model=Counter(cfg['backbone'],cfg['channels'],pretrained=False,head=cfg.get('head','dense'),offset=offset)
    missing,unexpected=model.load_state_dict(state,strict=False)
    assert not unexpected and ((missing and all(k.startswith('offset.') for k in missing)) if offset and not has else not missing),(missing,unexpected)
    if offset and not has:
        model.offset[0].load_state_dict(model.head[0].state_dict())
        with torch.no_grad():model.offset[-1].weight.zero_();model.offset[-1].bias.zero_()
    return model.to(device),payload

def training_view(image,record,entry,args,rng):
    """Scale augmentation: tray-centred zoom-in view with view_prob (needs a tray box), global zoom-out with zoom_out_prob."""
    u=rng.random()
    if entry and u<args.view_prob:
        fill,_=tp.tray_fill_zoom(image.size,entry['letterbox'],entry['tray_box'],args.view_margin);fill=min(4.,max(1.,fill))
        return crop_view(image,record,entry,rng.uniform((1+fill)/2,min(4.,1.1*fill)),rng,args.view_margin)
    if u<args.view_prob+args.zoom_out_prob:
        w,h=image.size;full={'tray_box':[0,0,w,h],'letterbox':{'width':w,'height':h,'left':0,'top':0}}
        return crop_view(image,record,full,rng.uniform(args.min_zoom_out,1.),rng,0.)
    return image,record

def summarize(rows):
    return {'images':len(rows),'exact_count_images':sum(r['predicted']==r['expected'] for r in rows),'perfect_localization_images':sum(r['fp']==0 and r['fn']==0 for r in rows),'count_mae':sum(abs(r['predicted']-r['expected']) for r in rows)/max(1,len(rows)),'tp':sum(r['tp'] for r in rows),'fp':sum(r['fp'] for r in rows),'fn':sum(r['fn'] for r in rows)}

def evaluate(model,records,images,job,step,args):
    """Standard letterbox pass (standard-XXXX.json) and the two-pass + merge pipeline (evaluation-XXXX.json, used for selection)."""
    model.eval();standard=[];pipeline=[];folder=job/f'check-{step:04d}';folder.mkdir(exist_ok=True)
    run_model=lambda canvas:model(torch.from_numpy(tp.to_array(canvas))[None].cuda())[0].float().cpu().numpy()
    with torch.inference_mode():
        for record in records:
            image=images[record['file']];x,target,_,_,box=prepare_v2(image,record,args.size,False)
            logits=model(x[None].cuda())[0].float().cpu().numpy();pred,rejected=decode_clean(logits);a,b,c=match(pred,target,tolerance=args.tolerance)
            standard.append({'file':record['file'],'split':record['split'],'expected':len(target),'predicted':len(pred),'tp':a,'fp':b,'fn':c,'predictions':pred,'rejected':rejected,'letterbox':box})
            result=tp.two_pass(run_model,image,margin=args.view_margin,ratio=args.merge_ratio,max_distance=args.max_distance,size=args.size)
            pred=result['predictions'];a,b,c=match(pred,target,tolerance=args.tolerance)
            pipeline.append({'file':record['file'],'split':record['split'],'expected':len(target),'predicted':len(pred),'tp':a,'fp':b,'fn':c,'predictions':pred,'rejected':result['rejected'],'removed':result['removed'],'letterbox':box,'zoom':result['zoom'],'rule':result['rule'],'tray_box':result['tray_box'],'standard_predicted':len(result['standard'])})
            if (b or c) and result['view'] is not None and step>0:
                canvas=result['view'];draw=ImageDraw.Draw(canvas);s=args.size/640
                for t in target:
                    u,v=tp.canvas_to_view(t,result['crop'],box,image.size);draw.ellipse((u-6*s,v-6*s,u+6*s,v+6*s),outline='lime',width=2)
                for q in pred:
                    u,v=q['crop_x'],q['crop_y'];draw.line((u-5*s,v,u+5*s,v),fill='magenta',width=2);draw.line((u,v-5*s,u,v+5*s),fill='magenta',width=2)
                for q in result['removed']:
                    u,v=q['crop_x'],q['crop_y'];draw.line((u-5*s,v-5*s,u+5*s,v+5*s),fill='red',width=2);draw.line((u-5*s,v+5*s,u+5*s,v-5*s),fill='red',width=2)
                canvas.save(folder/record['file'],quality=90)
    for name,rows in [('standard',standard),('evaluation',pipeline)]:
        summary={split:summarize([r for r in rows if r['split']==split]) for split in ['train','validation']}
        report={'step':step,'pipeline':'two-pass tray view + saddle merge' if name=='evaluation' else 'standard decoder','size':args.size,'summary':summary,'images':rows}
        (job/f'{name}-{step:04d}.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'evaluation_step':step,'pipeline':'two-pass' if name=='evaluation' else 'standard','summary':summary}),flush=True)
    return report

def main(args):
    root=Path(__file__).resolve().parent;job=root/args.run;manifest_path=root/args.manifest;manifest=json.loads(manifest_path.read_text());records=manifest['images']
    train=[r for r in records if r['eligible_for_training']];assert all(r['split']=='train' and r['label_status'] in ['human_reviewed','assistant_reviewed'] for r in train)
    evaluation=[r for r in records if r['split']!='test' and r['label_status'] in ['human_reviewed','assistant_reviewed']];require_label_audits(evaluation)
    view_boxes=load_view_boxes(root,args)
    pseudo=[]
    if args.pseudo_manifest:
        pseudo=[r for r in json.loads((root/args.pseudo_manifest).read_text())['images'] if r.get('pseudo') and r['split']=='train' and r['count']>0];assert pseudo and args.pseudo_per_batch>0
    pseudo_rng=random.Random(args.seed+4241)
    synthetic=[]
    if args.synthetic_manifest:
        synthetic=[r for r in json.loads((root/args.synthetic_manifest).read_text())['images'] if r.get('synthetic') and r['eligible_for_training'] and r['split']=='train' and r['count']>0];assert synthetic and args.synthetic_per_batch>0
    synthetic_rng=random.Random(args.seed+8483)
    if args.view_prob>0:assert view_boxes and all(r['file'] in view_boxes for r in train),'--view-boxes must cover every training image when --view-prob > 0'
    if args.tolerance is None:args.tolerance=10*args.size/640
    if args.max_distance is None:args.max_distance=64*args.size/640
    job.mkdir(exist_ok=False);images={r['file']:Image.open(root/'full-previews'/r['file']).convert('RGB') for r in evaluation}
    torch.set_num_threads(6);random.seed(args.seed);np.random.seed(args.seed);torch.manual_seed(args.seed)
    assert torch.cuda.is_available() and torch.cuda.mem_get_info()[0]>16*1024**3,'need 16 GiB free GPU memory'
    model=Counter(args.backbone,args.channels,not args.no_pretrained,args.head).cuda()
    if args.init_checkpoint:model.load_state_dict(torch.load(root/args.init_checkpoint,map_location='cpu',weights_only=False)['model'])
    config={**vars(args),'model':'timm '+args.backbone+' features 4-32 + bilinear FPN ('+str(args.channels)+' ch, '+args.head+' 3x3) + 2-channel stride-4 head','timm':timm.__version__,'torch':torch.__version__,'gpu':torch.cuda.get_device_name(),'parameters':sum(p.numel() for p in model.parameters()),'train_images':len(train),'dataset_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),'split_sha256':manifest['frozen_group_manifest_sha256'],'count_threshold':.3,'region_threshold':.5,'fast_components':FAST_COMPONENTS,'view_boxes_sha256':hashlib.sha256((root/args.view_boxes).read_bytes()).hexdigest() if args.view_boxes else None,'init_checkpoint_sha256':hashlib.sha256((root/args.init_checkpoint).read_bytes()).hexdigest() if args.init_checkpoint else None,'pseudo_images':len(pseudo),'synthetic_images':len(synthetic),'synthetic_manifest_sha256':hashlib.sha256((root/args.synthetic_manifest).read_bytes()).hexdigest() if args.synthetic_manifest else None,'synthetic_policy':'Synthetic Blender render records (exact physics labels, clean scenes only, never evaluated) replace the last synthetic_per_batch real draws of each batch from a separate RNG.' if synthetic else None,'pseudo_manifest_sha256':hashlib.sha256((root/args.pseudo_manifest).read_bytes()).hexdigest() if args.pseudo_manifest else None,'pseudo_policy':'Pseudo records (provisional three-model-agreement labels) replace the last pseudo_per_batch real draws of each batch; never evaluated.' if pseudo else None,'evaluation':'evaluation-XXXX.json = two-pass tray view + saddle merge (selection); standard-XXXX.json = plain letterbox; matching tolerance scaled from 10 px at 640; checkpoints model-XXXX.pt kept in the job folder and excluded from the results zip','selection':'External candidate selection over periodic checkpoints on validation; dense counts are training reconstruction; test groups never evaluated.'}
    (job/'config.json').write_text(json.dumps(config,indent=2));print(json.dumps(config),flush=True)
    report=evaluate(model,evaluation,images,job,0,args)
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay);start=time.time();history=[]
    lr_at=lambda step:args.lr*step/args.warmup if step<args.warmup else args.lr*.5*(1+math.cos(math.pi*(step-args.warmup)/max(1,args.steps-args.warmup)))
    for step in range(1,args.steps+1):
        for g in optimizer.param_groups:g['lr']=lr_at(step)
        model.train();xs=[];hs=[];ms=[];valid=[]
        batch=training_batch(train,report,random)
        if pseudo:batch=batch[:len(batch)-args.pseudo_per_batch]+pseudo_rng.sample(pseudo,args.pseudo_per_batch)
        if synthetic:batch=batch[:len(batch)-args.synthetic_per_batch]+synthetic_rng.sample(synthetic,args.synthetic_per_batch)
        for r in batch:
            if r['file'] not in images:images[r['file']]=Image.open(root/'full-previews'/r['file']).convert('RGB')
            image,record=training_view(images[r['file']],r,view_boxes.get(r['file']),args,random)
            x,points,mask,_,_=prepare_v2(image,record,args.size,True);xs.append(x);hs.append(heatmap(points,args.size,args.center_sigma));ms.append(mask);valid.append(record['region_supervised'])
        x=torch.stack(xs).cuda();target=torch.stack(hs).cuda();mask=torch.stack(ms).cuda();valid=torch.tensor(valid,device='cuda',dtype=torch.bool)
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast('cuda',dtype=torch.bfloat16):output=model(x)
        output=output.float();center=focal(output[:,:1],target);region=region_loss(output[valid,1:2],mask[valid]) if valid.any() else output.sum()*0;loss=center+.5*region
        assert torch.isfinite(loss),'non-finite loss';loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),10);optimizer.step()
        if step==1 or step%100==0:
            entry={'step':step,'center_loss':float(center.detach()),'region_loss':float(region.detach()),'lr':lr_at(step),'seconds':round(time.time()-start,1)};history.append(entry);print(json.dumps(entry),flush=True)
        if step%args.check_every==0 or step==args.steps:
            report=evaluate(model,evaluation,images,job,step,args)
            torch.save({'model':model.state_dict(),'config':config,'step':step},job/f'model-{step:04d}.pt')
    (job/'history.json').write_text(json.dumps(history,indent=2));(job/'runtime.json').write_text(json.dumps({'seconds':time.time()-start,'peak_gpu_bytes':torch.cuda.max_memory_allocated()}))
    with zipfile.ZipFile(root/(args.run+'-results.zip'),'w',zipfile.ZIP_DEFLATED) as archive:
        for path in job.rglob('*'):
            if path.is_file() and path.suffix!='.pt':archive.write(path,str(path.relative_to(job)))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);parser.add_argument('--manifest',required=True)
    parser.add_argument('--backbone',default='efficientnetv2_rw_s');parser.add_argument('--channels',type=int,default=128);parser.add_argument('--head',choices=['dense','separable'],default='dense');parser.add_argument('--no-pretrained',action='store_true')
    parser.add_argument('--size',type=int,default=1024);parser.add_argument('--steps',type=int,default=4000);parser.add_argument('--check-every',type=int,default=500);parser.add_argument('--seed',type=int,default=113)
    parser.add_argument('--lr',type=float,default=2e-4);parser.add_argument('--weight-decay',type=float,default=.02);parser.add_argument('--warmup',type=int,default=200);parser.add_argument('--center-sigma',type=float,default=1.3)
    parser.add_argument('--view-boxes');parser.add_argument('--view-prob',type=float,default=.5);parser.add_argument('--view-margin',type=float,default=.06);parser.add_argument('--zoom-out-prob',type=float,default=.2);parser.add_argument('--min-zoom-out',type=float,default=.7)
    parser.add_argument('--merge-ratio',type=float,default=.5);parser.add_argument('--init-checkpoint',help='start from these weights instead of ImageNet only');parser.add_argument('--pseudo-manifest');parser.add_argument('--pseudo-per-batch',type=int,default=0);parser.add_argument('--synthetic-manifest',help='synthetic render manifest (postprocess_synth_batch.py); clean train records only');parser.add_argument('--synthetic-per-batch',type=int,default=0);parser.add_argument('--tolerance',type=float);parser.add_argument('--max-distance',type=float)
    main(parser.parse_args())
