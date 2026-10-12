"""Diagnostic phase analysis; no changes to forces, COP or app behavior."""
import argparse,csv,hashlib,json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def runs(mask,t):
 change=np.diff(np.r_[False,mask,False].astype(int));start=np.where(change==1)[0];end=np.where(change==-1)[0]-1
 return [{'start_s':float(t[a]),'end_s':float(t[b]),'samples':int(b-a+1),'sample_span_s':float(t[b]-t[a]),'left_censored':bool(a==0),'right_censored':bool(b==len(t)-1)} for a,b in zip(start,end)]
def crossings(x,t,level):
 out=[]
 for i in range(1,len(t)):
  if (x[i-1]>level)!=(x[i]>level):
   tt=t[i-1]+(level-x[i-1])/(x[i]-x[i-1])*(t[i]-t[i-1]);out.append({'time_s':float(tt),'direction':'rising' if x[i]>x[i-1] else 'falling','bracket_s':[float(t[i-1]),float(t[i])]})
 return out

def force_hysteresis(f,on=100,off=50):
 state=False;out=[]
 for v in f:
  if state and v<=off:state=False
  elif not state and v>on:state=True
  out.append(state)
 return np.array(out)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();root=args.output;root.mkdir(parents=True,exist_ok=False)
before=sha(args.input);a=np.genfromtxt(args.input,delimiter=',',names=True);t=a['time'];assert len(t)==159 and np.all(np.diff(t)>0)
dt=float(np.median(np.diff(t)))
report={'scientific_accuracy_validated':False,'status':'diagnostic_transition_analysis_completed','source_sha256':before,'source_match':'regenerated_model_motion_and_existing_forces_not_identical_runs','rows':len(t),'sampling_interval_s':dt,'force_thresholds_N':[20,50,100,200],'height_thresholds_m':[0,.02,.05,.07],'floor_offset_sensitivity_m':[-.02,0,.02],'hysteresis_on_off_N':[100,50],'crossing_times':'linear interpolation between adjacent samples; not measured heel strike or toe off','COP_invalid_when_Fy_le_20N':True,'phase_labels_are_diagnostics_not_validated_contact_detection':True,'sides':{}}
all_rows=[]
fig,axs=plt.subplots(4,2,figsize=(13,12),sharex=True)
for j,(side,label) in enumerate([('right','Rechtervoet'),('left','Linkervoet')]):
 f=a[side+'_Fy'];h=a[side+'_min_gap'];dist=a[side+'_old_COP_distance'];support=force_hysteresis(f)
 low=(f>20)&(f<=100);high=f>100;near=h<=.02;raised=h>.05;robust_raised=h>.07
 # No COP interpretation below the upstream 20 N validity boundary.
 valid=f>20;phase=np.full(len(t),'kracht_te_laag_COP_onbruikbaar',dtype=object)
 phase[valid & low & near]='lichte_kracht_voet_nabij_grond'
 phase[valid & low & ~near]='lage_restkracht_voet_vrij'
 phase[high & near]='hoge_kracht_voet_nabij_grond'
 phase[high & ~near]='hoge_kracht_voet_niet_nabij_grond'
 sr={'force_threshold_intervals':{str(k):runs(f>k,t) for k in [20,50,100,200]},'height_above_intervals':{str(k):runs(h>k,t) for k in [0,.02,.05,.07]},'force_crossings':{str(k):crossings(f,t,k) for k in [20,50,100]},'height_crossings':{str(k):crossings(h,t,k) for k in [.02,.05]},'hysteresis_intervals':runs(support,t),'phase_counts':{str(v):int(np.sum(phase==v)) for v in np.unique(phase)},'force_band_COP':{},'floor_sensitive_contact_samples':int(np.sum((h>0)&(h<=.04))),'low_force_while_above_5cm_samples':int(np.sum(low&raised)),'low_force_while_above_7cm_samples':int(np.sum(low&robust_raised)),'negative_Fy_samples':int(np.sum(f<0))}
 for lo,hi in [(20,50),(50,100),(100,float('inf'))]:
  mask=(f>lo)&(f<=hi);d=dist[mask]
  sr['force_band_COP'][f'{lo}-{hi}']={'samples':int(mask.sum()),'outside_3cm_samples':int(np.sum(d>.03)),'median_distance_m':float(np.median(d)) if len(d) else None,'max_distance_m':float(np.max(d)) if len(d) else None}
 for offset in [-.02,0,.02]:
  sr.setdefault('floor_sensitivity',{})[str(offset)]={'height_2cm_crossings':crossings(h-offset,t,.02),'high_force_not_near_samples':int(np.sum(high&(h-offset>.02))),'low_force_raised_5cm_samples':int(np.sum(low&(h-offset>.05)))}
 # Show raw transition brackets rather than enforcing a universal timing lag.
 if side=='right':
  down=next(x['time_s'] for x in sr['force_crossings']['100'] if x['direction']=='falling')
  up=next(x['time_s'] for x in sr['force_crossings']['100'] if x['direction']=='rising')
  lift=next(x['time_s'] for x in sr['height_crossings']['0.02'] if x['direction']=='rising')
  land=next(x['time_s'] for x in sr['height_crossings']['0.02'] if x['direction']=='falling')
  sr['transition_comparison_s']={'force_below_100':down,'foot_above_2cm':lift,'force_above_100':up,'foot_back_below_2cm':land,'unloading_precedes_height_crossing':lift-down,'loading_precedes_height_crossing':land-up}
  air=runs(raised,t);sr['main_raised_interval']=max(air,key=lambda x:x['samples'])
 events=np.array([v['time_s'] for v in sr['force_crossings']['100']])
 event_distance=np.min(abs(t[:,None]-events),axis=1) if len(events) else np.full(len(t),np.inf)
 high_bad=high&(dist>.03)
 sr['transition_window_sensitivity']={str(window):{'high_force_samples_near_transition':int(np.sum(high&(event_distance<=window))),'high_force_COP_outliers_near_transition':int(np.sum(high_bad&(event_distance<=window))),'high_force_samples_outside_window':int(np.sum(high&(event_distance>window))),'high_force_COP_outliers_outside_window':int(np.sum(high_bad&(event_distance>window)))} for window in [.05,.1,.15]}
 report['sides'][side]=sr
 for i in range(len(t)):all_rows.append([side,t[i],f[i],h[i],dist[i] if valid[i] else '',int(valid[i]),int(support[i]),int(near[i]),int(raised[i]),int(robust_raised[i]),phase[i],int(h[i]<-.02),event_distance[i],int(event_distance[i]<=.1),int(0<h[i]<=.04)])
 ax=axs[0,j];ax.plot(t,f,color='#0072B2');ax.axhline(100,color='gray',ls='--',label='100 N');ax.axhline(50,color='gray',ls=':',label='50 N');ax.set_title(label);ax.set_ylabel('Voorspelde Fy (N)');ax.legend(fontsize=8)
 ax=axs[1,j];ax.plot(t,f,color='#0072B2');ax.set_ylim(-20,110);ax.axhline(20,color='gray',ls=':',label='20 N');ax.axhline(50,color='gray',ls='--');ax.axhline(100,color='gray',ls='--');ax.fill_between(t,-20,110,where=raised,color='#D55E00',alpha=.12,label='Voet >5 cm vrij');ax.set_ylabel('Fy: lage-krachtgebied (N)');ax.legend(fontsize=8)
 ax=axs[2,j];ax.plot(t,h*100,color='#D55E00');ax.axhline(2,color='gray',ls=':');ax.axhline(5,color='gray',ls='--');ax.fill_between(t,(h-.02)*100,(h+.02)*100,color='#D55E00',alpha=.12,label='Vloerhoogte ±2 cm');ax.set_ylabel('Laagste contactbol (cm)');ax.legend(fontsize=8)
 ax=axs[3,j];ax.plot(t,np.where(valid,dist*100,np.nan),color='#0072B2',label='Bestaande COP');ax.scatter(t[high],dist[high]*100,color='#009E73',s=10,label='Fy >100 N');ax.axhline(3,color='gray',ls=':');ax.set_ylabel('COP buiten voetcontour (cm)');ax.set_xlabel('Tijd (s)');ax.legend(fontsize=8)
for ax in axs.flat:ax.grid(alpha=.2)
fig.suptitle('Contactovergangen en lage-krachtfasen',fontsize=17);fig.text(.06,.012,'Onderzoek: voorspelde krachten + herberekend model. Grenzen zijn gevoeligheidskeuzes, geen gemeten contactdetectie.\nCOP bij Fy ≤20 N niet beoordeeld; afkapmomenten van de opname blijven onvolledige fasen.',fontsize=9);fig.tight_layout(rect=[0,.055,1,.95])
for ext in ['png','pdf']:fig.savefig(root/('contactovergangen.'+ext),dpi=160)
plt.close(fig)
fig,axs=plt.subplots(2,1,figsize=(13,6),sharex=True)
for ax,(side,label) in zip(axs,[('right','Rechts'),('left','Links')]):
 f=a[side+'_Fy'];h=a[side+'_min_gap']
 masks=[f>20,f>50,f>100,force_hysteresis(f),h<=.02,h<=.05]
 labels=['Kracht >20 N','Kracht >50 N','Kracht >100 N','Kracht: aan >100 / uit ≤50 N','Voet binnen 2 cm','Voet binnen 5 cm']
 for k,(mask,lab) in enumerate(zip(masks,labels)):
  ax.broken_barh([(r['start_s']-dt/2,r['samples']*dt) for r in runs(mask,t)],(k-.3,.6),facecolors=('#0072B2' if k<4 else '#D55E00'))
 ax.set_yticks(range(6),labels);ax.set_ylim(-.6,5.6);ax.set_title(label);ax.grid(axis='x',alpha=.2)
axs[-1].set_xlabel('Tijd (s)');fig.suptitle('Verschillende grenzen geven verschillende contactfasen',fontsize=16);fig.text(.06,.015,'Deze balken zijn diagnostische maskers. Voetnabijheid is niet automatisch werkelijk contact; krachtmaskers zijn modelvoorspellingen.',fontsize=9);fig.tight_layout(rect=[0,.06,1,.93])
for ext in ['png','pdf']:fig.savefig(root/('contactmaskers.'+ext),dpi=160)
plt.close(fig)
with (root/'fasecontrole.csv').open('w',newline='') as f:
 w=csv.writer(f,lineterminator='\n');w.writerow(['side','time','Fy_N','minimum_gap_m','COP_distance_m_only_when_Fy_gt_20','COP_assessable','hysteresis_force_candidate','near_floor_2cm','above_floor_5cm','above_floor_7cm','diagnostic_phase','penetration_more_than_2cm','distance_to_100N_crossing_s','within_100ms_force_transition','near_floor_classification_sensitive_to_plus_minus_2cm']);w.writerows(all_rows)
report['source_preserved']=sha(args.input)==before
(root/'rapport.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({s:{'transitions':v.get('transition_comparison_s'),'bands':v['force_band_COP'],'phase_counts':v['phase_counts'],'hysteresis':v['hysteresis_intervals']} for s,v in report['sides'].items()},indent=2))
