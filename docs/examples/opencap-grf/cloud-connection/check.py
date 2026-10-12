"""Research-only check on regenerated augmented markers and existing demo GRFs."""
from pathlib import Path
import argparse,hashlib,importlib.util,inspect,json,shutil
import numpy as np
import torch
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
parser=argparse.ArgumentParser(description=__doc__)
for name in ['project','inputs','candidate','reconverted-motion','output']:
 parser.add_argument('--'+name,type=Path,required=True)
args=parser.parse_args()
ROOT=args.output.resolve();ROOT.mkdir(parents=True,exist_ok=False)
P=args.project.resolve()
OLD=args.inputs.resolve()
C=args.candidate.resolve()/'OpenSimPipeline/ForGaitDynamics'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def mot(p):
 s=p.read_text().splitlines();i=s.index('endheader');return s[i+1].split(),np.loadtxt(s[i+2:])
source=next((P/'pose-3d').glob('*LSTM.trc'))
s=source.read_text().splitlines();meta=dict(zip(s[1].split(),s[2].split()));names=s[3].split()[2:];a=np.loadtxt(s[5:]);tm=a[:,1];markers=a[:,2:].reshape(len(a),len(names),3)
assert meta['Units']=='m' and len(a)==int(meta['NumFrames']) and len(names)==65
marker=lambda n:markers[:,names.index(n),:]
forward=(marker('r.ASIS_study')+marker('L.ASIS_study')-marker('r.PSIS_study')-marker('L.PSIS_study'))/2
f=forward.mean(axis=0);yaw=np.arctan2(f[2],f[0]);R=np.array([[np.cos(yaw),0,np.sin(yaw)],[0,1,0],[-np.sin(yaw),0,np.cos(yaw)]])
origin=((marker('r.ASIS_study')+marker('L.ASIS_study'))/2).mean(axis=0);origin[1]=0
aligned=(markers-origin)@R.T
mapping={'r_calc_study':'r_calc','r_toe_study':'r_toe','L_calc_study':'L_calc','L_toe_study':'L_toe'}
chosen=list(mapping)
trc=ROOT/'markers-aligned.trc'
with trc.open('w') as out:
 out.write('PathFileType\t4\t(X/Y/Z)\tmarkers-aligned.trc\n'+s[1]+'\n')
 out.write(f'60\t60\t{len(a)}\t4\tm\t60\t1\t{len(a)}\n')
 out.write('Frame#\tTime\t'+'\t\t\t'.join(mapping.values())+'\t\t\n')
 out.write('\t\t'+'\t'.join(f'{ax}{i}' for i in range(1,5) for ax in 'XYZ')+'\n\n')
 np.savetxt(out,np.column_stack((a[:,:2],aligned[:,[names.index(n) for n in chosen],:].reshape(len(a),-1))),delimiter='\t')
gn,g=mot(OLD/'ground_reaction_forces.mot'); t=g[:,0]
assert np.all(np.diff(tm)>0) and t.min()>=tm.min()-1e-8 and t.max()<=tm.max()+1e-8
originalhash={n:sha(OLD/n) for n in ['ground_reaction_forces.mot','model.osim','motion.mot']}
transformed=g.copy()
for side in [1,2]:
 for suffix,ispoint in [('v',False),('p',True)]:
  idx=[gn.index(f'force{side}_{suffix}{axis}') for axis in 'xyz'];v=g[:,idx]
  transformed[:,idx]=(v-origin if ispoint else v)@R.T
 idx=[gn.index(f'torque{side}_{axis}') for axis in 'xyz'];transformed[:,idx]=g[:,idx]@R.T
newnames=[n.replace('force1_','R_ground_force_').replace('force2_','L_ground_force_').replace('torque1_','R_ground_torque_').replace('torque2_','L_ground_torque_') for n in gn]
grfin=ROOT/'forces-disposable.mot'
with grfin.open('w') as out:
 out.write(f'nRows={len(g)}\nnColumns={len(gn)}\nendheader\n'+'\t'.join(newnames)+'\n');np.savetxt(out,transformed,delimiter='\t')
spec=importlib.util.spec_from_file_location('candidate',C/'COP_Predictor.py');cop=importlib.util.module_from_spec(spec);spec.loader.exec_module(cop)
fntext=inspect.getsource(cop.predict_cop_for_trial);assert fntext.count('/ 1_000_000.0')==2
scope=dict(cop.__dict__);exec(fntext.replace('/ 1_000_000.0','/ 1000.0'),scope)
with torch.serialization.safe_globals([np.core.multiarray._reconstruct,np.ndarray,np.dtype,type(np.dtype('float64')),type(np.dtype('float32'))]):
 scope['predict_cop_for_trial'](str(grfin),str(trc),trial_name='cloud-connection',artifact_path=str(C/'cop_mlp_ar.pt'),output_grf_path=str(ROOT/'cop-aligned-experimental.mot'),grf_threshold_N=20)
out=cop.load_mot_to_struct(ROOT/'cop-aligned-experimental.mot')
def interp(n):return np.column_stack([np.interp(t,tm,aligned[:,names.index(n),j]) for j in range(3)])
def distance_triangle(p,tri):
 cross=[np.cross(tri[(i+1)%3]-tri[i],p-tri[i]).item() for i in range(3)]
 if all(v>=0 for v in cross) or all(v<=0 for v in cross):return 0.
 vals=[]
 for i in range(3):
  u,v=tri[i],tri[(i+1)%3];d=v-u;lam=np.clip(np.dot(p-u,d)/np.dot(d,d),0,1);vals.append(np.linalg.norm(p-(u+lam*d)))
 return min(vals)
report={'status':'technical_probe_completed','scientific_accuracy_validated':False,'source_match':'same_pinned_demo_regenerated_not_bit_identical',
 'pose2sim_commit':'68304b47195dac80bc341cdfd9669140609358c4','candidate_commit':'6369b19d9b53fa357befe651401af25b8350622d',
 'augmented_marker_file_sha256':sha(source),'source_sha256':originalhash,'marker_mapping':mapping,'marker_units':'m','cop_checkpoint_sha256':sha(C/'cop_mlp_ar.pt'),
 'marker_rows':len(a),'marker_count':len(names),'force_rows':len(g),'marker_time_s':[float(tm[0]),float(tm[-1])],
 'force_time_s':[float(t[0]),float(t[-1])],'extrapolation_used':False,'heading_rotation_degrees':float(np.rad2deg(yaw)),
 'rotation_matrix':R.tolist(),'translation_origin_m':origin.tolist(),'stance_threshold_N':20,
 'model_unit_correction_diagnostic_only':True,'new_grf_prediction_blocked':'model download HTTP403 on media.githubusercontent.com and huggingface.co',
 'stance_is_inferred_from_unvalidated_predicted_forces':True,'sides':{}}
fig,axs=plt.subplots(2,2,figsize=(12,9))
for i,(prefix,short,idx,label) in enumerate([('R','r',1,'Rechtervoet'),('L','L',2,'Linkervoet')]):
 heel=interp(short+'_calc_study');toe=interp(short+'_toe_study');meta5=interp(short+'_5meta_study')
 cp=np.column_stack([out[prefix+'_ground_force_p'+axis] for axis in 'xyz']);base=transformed[:,[gn.index(f'force{idx}_p{axis}') for axis in 'xyz']]
 mask=g[:,gn.index(f'force{idx}_vy')]>20
 dist=np.array([distance_triangle(cp[j,[0,2]],np.array([heel[j,[0,2]],toe[j,[0,2]],meta5[j,[0,2]]])) for j in range(len(t))])
 based=np.array([distance_triangle(base[j,[0,2]],np.array([heel[j,[0,2]],toe[j,[0,2]],meta5[j,[0,2]]])) for j in range(len(t))])
 result={'predicted_stance_samples':int(mask.sum()),'cop_outside_marker_triangle_more_than_3cm_samples':int(np.sum(dist[mask]>.03)),
 'cop_distance_from_marker_triangle_median_m':float(np.median(dist[mask])),'cop_distance_from_marker_triangle_max_m':float(np.max(dist[mask])),
 'original_cop_distance_from_marker_triangle_median_m':float(np.median(based[mask])),
 'cop_py_retained_from_existing_grf':True,'cop_pz_retained_in_aligned_frame_from_existing_grf':True}
 report['sides'][label]=result
 ax=axs[i,0];ax.plot(t,heel[:,0],label='Hiel');ax.plot(t,toe[:,0],label='Teen');ax.plot(t,np.where(mask,cp[:,0],np.nan),label='COP-proef');ax.plot(t,np.where(mask,base[:,0],np.nan),ls=':',label='Bestaande COP')
 ax.set_title(label+' — positie langs uitgelijnde X-as');ax.set_ylabel('m');ax.set_xlabel('Tijd (s)');ax.legend(fontsize=8);ax.grid(alpha=.2)
 ax=axs[i,1];ax.plot(t,np.where(mask,dist,np.nan)*100,label='COP-proef');ax.plot(t,np.where(mask,based,np.nan)*100,ls=':',label='Bestaande COP');ax.axhline(3,color='gray',ls='--',label='Marge 3 cm');ax.set_title(label+' — afstand tot markerdriehoek');ax.set_ylabel('cm');ax.set_xlabel('Tijd (s)');ax.legend(fontsize=8);ax.grid(alpha=.2)
report['original_files_preserved']=all(sha(OLD/n)==v for n,v in originalhash.items())
report['finite_output']=all(np.isfinite(v).all() for v in out.values())
report['non_px_columns_preserved']=all(np.allclose(transformed[:,j],out[n],atol=1e-10,rtol=0) for j,n in enumerate(newnames) if n not in ['time','R_ground_force_px','L_ground_force_px'])
newn,newm=mot(args.reconverted_motion.resolve());oldn,oldm=mot(OLD/'motion.mot')
assert newn==oldn and np.allclose(newm[:,0],oldm[:,0],rtol=0,atol=1e-6)
report['regenerated_motion_comparison']={'same_sample_times':True,'max_translation_difference_m':{n:float(np.max(abs(newm[:,newn.index(n)]-oldm[:,oldn.index(n)]))) for n in ['pelvis_tx','pelvis_ty','pelvis_tz']},'max_rotation_difference_deg':float(np.max(abs(np.delete(newm-oldm,[0,4,5,6],axis=1))))}
fig.suptitle('Aansluitingsproef met uitgebreide Pose2Sim-markers',fontsize=16)
fig.text(.07,.02,'Technische proef • evenwichtsoefening • herberekende markers + bestaande voorspelde krachten\nMarkerdriehoek is een ruwe geometrische controle, geen gemeten drukvlak. Alleen COP-X opnieuw voorspeld.',fontsize=9)
fig.tight_layout(rect=[0,.07,1,.94]);fig.savefig(ROOT/'aansluitingsproef.png',dpi=160);fig.savefig(ROOT/'aansluitingsproef.pdf');plt.close(fig)
(ROOT/'connection-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
