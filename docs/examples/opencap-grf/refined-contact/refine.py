"""Research-only contact geometry/COP/COM check; no dynamics optimization."""
import argparse,csv,hashlib,json,xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
import opensim as osim
from scipy.spatial import ConvexHull
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

def read_mot(p):
 s=p.read_text().splitlines();e=next(i for i,v in enumerate(s) if v.strip()=='endheader');return s[e+1].split(),np.loadtxt(s[e+2:])
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def hull(points):return points[ConvexHull(points).vertices]
def distance(point,poly):
 a=poly;b=np.roll(poly,-1,axis=0);v=b-a;w=point-a
 cross=v[:,0]*w[:,1]-v[:,1]*w[:,0]
 if np.all(cross>=-1e-10):return 0.
 lam=np.clip(np.sum(v*w,axis=1)/np.sum(v*v,axis=1),0,1)
 return float(np.min(np.linalg.norm(point-(a+lam[:,None]*v),axis=1)))
def summ(arr,mask):
 x=arr[mask & np.isfinite(arr)]
 return {'count':len(x),'median':float(np.median(x)) if len(x) else None,'max':float(np.max(x)) if len(x) else None,'outside_3cm_count':int(np.sum(x>.03))}
parser=argparse.ArgumentParser(description=__doc__)
for n in ['source','forces','experimental','output']:parser.add_argument('--'+n,type=Path,required=True)
args=parser.parse_args();root=args.source;out=args.output;out.mkdir(parents=True,exist_ok=False)
paths=[root/'model-Pose2Sim.osim',root/'motion-Pose2Sim.mot',args.forces,args.experimental,root/'connection-report.json'];hashes={str(p):digest(p) for p in paths}
mn,m=read_mot(paths[1]);gn,g=read_mot(args.forces);en,e=read_mot(args.experimental);t=g[:,0];assert np.allclose(t,e[:,0],atol=1e-8,rtol=0)
assert t.min()>=m[0,0]-1e-8 and t.max()<=m[-1,0]+1e-8
meta=json.loads((root/'connection-report.json').read_text());R=np.array(meta['rotation_matrix']);origin=np.array(meta['translation_origin_m'])
F=np.stack([g[:,[gn.index(f'force{j}_v{ax}') for ax in 'xyz']] for j in [1,2]],axis=1)
P=np.stack([g[:,[gn.index(f'force{j}_p{ax}') for ax in 'xyz']] for j in [1,2]],axis=1)
T=np.stack([g[:,[gn.index(f'torque{j}_{ax}') for ax in 'xyz']] for j in [1,2]],axis=1)
Q=np.stack([e[:,[en.index(f'{side}_ground_force_p{ax}') for ax in 'xyz']]@R+origin for side in ['R','L']],axis=1)
assert np.allclose(Q[:,:,1],P[:,:,1],atol=1e-10)
loaded=F[:,:,1]>20
xml=ET.parse(paths[0]);floor=next(xml.iter('ContactHalfSpace'))
assert floor.findtext('socket_frame')=='/ground'
assert np.allclose(np.fromstring(floor.findtext('location'),sep=' '),0)
assert np.allclose(np.fromstring(floor.findtext('orientation'),sep=' '),[0,0,-np.pi/2])
spheres=[]
for v in xml.iter('ContactSphere'):
 spheres.append({'name':v.attrib['name'],'body':v.findtext('socket_frame').split('/')[-1],'location':np.fromstring(v.findtext('location'),sep=' '),'radius':float(v.findtext('radius')),'side':0 if v.attrib['name'].endswith('_r') else 1})
osim.Logger.setLevelString('error');model=osim.Model(str(paths[0]));state=model.initSystem();coords=model.getCoordinateSet()
values=m[:,1:].copy()
for j,n in enumerate(mn[1:]):
 if coords.get(n).getMotionType()!=osim.Coordinate.Translational:values[:,j]=np.unwrap(np.deg2rad(values[:,j]))
q=np.column_stack([np.interp(t,m[:,0],values[:,j]) for j in range(values.shape[1])])
centres=np.zeros((len(t),len(spheres),3));com=np.zeros((len(t),3))
for i,tt in enumerate(t):
 state.setTime(float(tt))
 for n,v in zip(mn[1:],q[i]):coords.get(n).setValue(state,float(v),False)
 model.realizePosition(state)
 c=model.calcMassCenterPosition(state);com[i]=[c.get(j) for j in range(3)]
 for j,s in enumerate(spheres):
  p=model.getBodySet().get(s['body']).findStationLocationInGround(state,osim.Vec3(*s['location']));centres[i,j]=[p.get(k) for k in range(3)]
radii=np.array([s['radius'] for s in spheres]);gap=centres[:,:,1]-radii
angles=np.linspace(0,2*np.pi,64,endpoint=False);circle=np.column_stack((np.cos(angles),np.sin(angles)))
footpoly=[[],[]];groundpoly=[];potential=[];minimum_gap=np.zeros((len(t),2));d_old=np.zeros((len(t),2));d_new=d_old.copy()
for i in range(len(t)):
 ground=[];pot=[]
 for side in [0,1]:
  inds=[j for j,s in enumerate(spheres) if s['side']==side]
  minimum_gap[i,side]=np.min(gap[i,inds])
  cloud=np.concatenate([centres[i,j,[0,2]]+radii[j]*circle for j in inds]);poly=hull(cloud);footpoly[side].append(poly)
  d_old[i,side]=distance(P[i,side,[0,2]],poly);d_new[i,side]=distance(Q[i,side,[0,2]],poly)
  if loaded[i,side]:pot.append(cloud)
  for j in inds:
   if gap[i,j]<=.02:ground.append(centres[i,j,[0,2]]+radii[j]*circle)
 potential.append(hull(np.concatenate(pot)) if pot else None)
 groundpoly.append(hull(np.concatenate(ground)) if ground else None)
w=np.where(loaded,F[:,:,1],0);den=w.sum(axis=1);valid=den>0
weighted_old=np.full((len(t),3),np.nan);weighted_new=weighted_old.copy();total_old=weighted_old.copy();total_new=weighted_old.copy()
for pts,weighted,total in [(P,weighted_old,total_old),(Q,weighted_new,total_new)]:
 weighted[valid]=np.sum(w[:,:,None]*pts,axis=1)[valid]/den[valid,None]
 moment=np.sum(np.where(loaded[:,:,None],np.cross(pts,F)+T,0),axis=1)
 total[valid,0]=moment[valid,2]/den[valid];total[valid,1]=0;total[valid,2]=-moment[valid,0]/den[valid]
combined={}
for label,points in [('original_total',total_old),('experimental_total',total_new),('com_projection',com)]:
 for polylabel,polys in [('force_selected_envelope',potential),('ground_proximal_envelope',groundpoly)]:
  combined[label+'_'+polylabel]=np.array([distance(points[i,[0,2]],p) if p is not None and np.isfinite(points[i]).all() else np.nan for i,p in enumerate(polys)])
report={'scientific_accuracy_validated':False,'status':'refined_geometric_check_completed','source_match':'regenerated_markers_model_and_existing_forces_not_bit_identical_runs','mass_kg':float(model.getTotalMass(state)),'rows':len(t),'units':{'length':'m','force':'N','time':'s'},'floor_y_m':0,'contact_sphere_count':len(spheres),'sphere_radii_m':radii.tolist(),'source_sha256':hashes,'no_extrapolation':True,'ground_proximity_threshold_m':.02,'force_threshold_N':20,'cop_distance_margin_m':.03,'contact_envelope':'convex hull of full horizontally projected contact-sphere discs; conservative, not true pressure patch','total_cop_method':'sum of r cross F plus free torque about world origin; intersect resultant normal moment with y=0; feet with Fy<=20N excluded','com_method':'OpenSim mass-weighted centre of full model; horizontal projection','negative_Fy_samples':{side:int(np.sum(F[:,i,1]<0)) for i,side in enumerate(['right','left'])},'per_foot':{},'combined':{k:summ(v,np.ones(len(t),dtype=bool)) for k,v in combined.items()}}
for side,label in enumerate(['right','left']):
 mask=loaded[:,side]
 report['per_foot'][label]={'predicted_loaded_samples':int(mask.sum()),'lowest_sphere_gap_m':{'min':float(minimum_gap[:,side].min()),'max':float(minimum_gap[:,side].max()),'median_when_loaded':float(np.median(minimum_gap[mask,side]))},'loaded_but_more_than_2cm_above_floor_samples':int(np.sum(mask&(minimum_gap[:,side]>.02))),'loaded_but_more_than_5cm_above_floor_samples':int(np.sum(mask&(minimum_gap[:,side]>.05))),'existing_cop_distance_m':summ(d_old[:,side],mask),'experimental_cop_distance_m':summ(d_new[:,side],mask),'force_peak_N':float(F[:,side,1].max())}
 for offset in [-.02,0,.02]:report['per_foot'][label].setdefault('floor_offset_sensitivity',{})[str(offset)] =int(np.sum(mask & (minimum_gap[:,side]-offset>.02)))
 report['per_foot'][label]['COP_y_when_loaded_m']={'min':float(P[mask,side,1].min()),'max':float(P[mask,side,1].max())}
 report['per_foot'][label]['force_threshold_sensitivity']={str(th):{'loaded_count':int(np.sum(F[:,side,1]>th)),'existing_COP_outside_3cm_count':int(np.sum((F[:,side,1]>th)&(d_old[:,side]>.03))),'experimental_COP_outside_3cm_count':int(np.sum((F[:,side,1]>th)&(d_new[:,side]>.03))),'foot_above_5cm_count':int(np.sum((F[:,side,1]>th)&(minimum_gap[:,side]>.05)))} for th in [20,50,100,200]}
 flying=mask&(minimum_gap[:,side]>.05)
 report['per_foot'][label]['force_when_foot_above_5cm_N']={'max':float(np.max(F[flying,side,1])) if np.any(flying) else None,'median':float(np.median(F[flying,side,1])) if np.any(flying) else None}
excluded=np.maximum(F[:,:,1],0).sum(axis=1)-den
report['max_excluded_positive_Fy_fraction']=float(np.max(excluded/np.maximum(np.maximum(F[:,:,1],0).sum(axis=1),1e-9)))
report['weighted_vs_resultant_total_COP_max_horizontal_difference_m']=float(np.nanmax(np.linalg.norm((weighted_old-total_old)[:,[0,2]],axis=1)))
report['original_files_preserved']=all(digest(Path(p))==v for p,v in hashes.items())
# Tabular evidence.
cols=['time','COM_x','COM_y','COM_z','right_Fy','left_Fy','right_min_gap','left_min_gap','right_old_COP_distance','left_old_COP_distance','right_new_COP_distance','left_new_COP_distance','total_old_COP_x','total_old_COP_z','total_new_COP_x','total_new_COP_z',*combined]
a=np.column_stack((t,com,F[:,:,1],minimum_gap,d_old,d_new,total_old[:,[0,2]],total_new[:,[0,2]],*[combined[k] for k in combined]))
with (out/'controle.csv').open('w',newline='') as f:writer=csv.writer(f,lineterminator='\n');writer.writerow(cols);writer.writerows(a)
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,axs=plt.subplots(3,2,figsize=(12,10),sharex=True)
for side,label in enumerate(['Rechtervoet','Linkervoet']):
 ax=axs[0,side];ax.plot(t,F[:,side,1]);ax.axhline(20,color='gray',ls=':');ax.set_title(label);ax.set_ylabel('Voorspelde verticale kracht (N)')
 bad=loaded[:,side]&(minimum_gap[:,side]>.02);ax.scatter(t[bad],F[bad,side,1],s=9,color='#D55E00',label='Kracht >20 N, voet >2 cm vrij');ax.legend(fontsize=8)
 ax=axs[1,side];ax.plot(t,minimum_gap[:,side]*100);ax.axhline(0,color='gray');ax.axhline(2,color='gray',ls=':');ax.set_ylabel('Onderkant laagste contactbol (cm)')
 ax=axs[2,side];ax.plot(t,np.where(loaded[:,side],d_old[:,side],np.nan)*100,label='Bestaande COP');ax.plot(t,np.where(loaded[:,side],d_new[:,side],np.nan)*100,label='COP-proef');ax.axhline(3,color='gray',ls=':');ax.set_ylabel('Afstand tot ruime voetcontour (cm)');ax.set_xlabel('Tijd (s)');ax.legend(fontsize=8)
for ax in axs.flat:ax.grid(alpha=.2)
fig.suptitle('Voetcontact, voorspelde kracht en COP per voet',fontsize=16);fig.text(.07,.015,'Modelgeometrie is een ruime benadering. Herberekend model + eerdere voorspelde krachten; geen validatie.',fontsize=9);fig.tight_layout(rect=[0,.04,1,.95])
for ext in ['png','pdf']:fig.savefig(out/('voetcontact-controle.'+ext),dpi=160)
plt.close(fig)
fig,axs=plt.subplots(2,2,figsize=(12,10));colors=['#0072B2','#D55E00']
for ax,tt in zip(axs.flat,[.10,.60,1.10,1.50]):
 i=int(np.argmin(abs(t-tt)))
 for side in [0,1]:
  poly=footpoly[side][i];ax.add_patch(Polygon(poly,facecolor=colors[side],edgecolor=colors[side],alpha=.25 if loaded[i,side] else .08));ax.plot(poly[:,0],poly[:,1],color=colors[side])
  if loaded[i,side]:ax.scatter(P[i,side,0],P[i,side,2],color=colors[side],marker='o',label=('COP rechts' if side==0 else 'COP links'));ax.scatter(Q[i,side,0],Q[i,side,2],color=colors[side],marker='x')
 if potential[i] is not None:
  poly=potential[i];ax.plot(np.r_[poly[:,0],poly[0,0]],np.r_[poly[:,1],poly[0,1]],color='gray',ls='--',lw=1)
 ax.scatter(total_old[i,0],total_old[i,2],marker='*',s=130,color='#9467bd',label='Gezamenlijk COP');ax.scatter(com[i,0],com[i,2],marker='+',s=100,color='black',label='Zwaartepuntprojectie')
 ax.set_title(f'{t[i]:.2f} s · Fy R/L {F[i,0,1]:.0f}/{F[i,1,1]:.0f} N');ax.set_xlabel('Grond-as X (m)');ax.set_ylabel('Grond-as Z (m)');ax.set_aspect('equal',adjustable='datalim');ax.grid(alpha=.2);ax.legend(fontsize=8)
fig.suptitle('Bovenaanzicht: COP en zwaartepunt zijn verschillende punten',fontsize=16);fig.text(.07,.015,'Voetcontour = projectie contactbollen, ook als de voet zweeft. Kruis in voetkleur = COP-proef.\nStippellijn = omhullende van voeten met voorspelde kracht >20 N; dit is geen gemeten steunvlak.',fontsize=9);fig.tight_layout(rect=[0,.06,1,.95])
for ext in ['png','pdf']:fig.savefig(out/('cop-en-zwaartepunt.'+ext),dpi=160)
plt.close(fig)
fig,axs=plt.subplots(2,1,figsize=(12,7),sharex=True)
for ax,env,title in zip(axs,['force_selected_envelope','ground_proximal_envelope'],['Ruime omhullende van voorspeld belaste voeten','Ruime omhullende van contactbollen binnen 2 cm van de grond']):
 for key,label in [('original_total','Gezamenlijk COP — bestaand'),('experimental_total','Gezamenlijk COP — proef'),('com_projection','Zwaartepuntprojectie')]:ax.plot(t,combined[key+'_'+env]*100,label=label)
 ax.axhline(3,color='gray',ls=':');ax.set_title(title);ax.set_ylabel('Afstand buiten omhullende (cm)');ax.grid(alpha=.2);ax.legend(fontsize=8)
axs[-1].set_xlabel('Tijd (s)');fig.suptitle('Gezamenlijk COP versus zwaartepuntprojectie',fontsize=16);fig.text(.07,.015,'Zwaartepuntprojectie buiten de omhullende kan bij dynamische balans voorkomen. COP buiten echte steun vraagt controle.\nDeze omhullenden zijn modelschattingen; geen grondwaarheid of stabiliteitsdiagnose.',fontsize=9);fig.tight_layout(rect=[0,.07,1,.95])
for ext in ['png','pdf']:fig.savefig(out/('gezamenlijke-controle.'+ext),dpi=160)
plt.close(fig)
(out/'rapport.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
