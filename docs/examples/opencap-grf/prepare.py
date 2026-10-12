"""Prepare disposable compatibility inputs; body origins are marker proxies only."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import opensim

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--inputs', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
ROOT, OUT = args.inputs.resolve(), args.output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
for filename in ['marker-proxies-m.trc', 'forces-renamed.mot', 'preparation.json']:
    if (OUT/filename).exists():
        raise FileExistsError(f'Use a fresh output directory: {OUT/filename}')

def read(path):
    lines = path.read_text().splitlines()
    end = next(i for i, s in enumerate(lines) if s.strip() == 'endheader')
    return lines[end+1].split(), np.loadtxt(lines[end+2:])

names, data = read(ROOT/'motion.mot')
model = opensim.Model(str(ROOT/'model.osim'))
state = model.initSystem()
coordinates = model.getCoordinateSet()
points = []
for row in data:
    state.setTime(float(row[0]))
    for name, value in zip(names[1:], row[1:]):
        coord = coordinates.get(name)
        coord.setValue(state, float(value) if coord.getMotionType() == opensim.Coordinate.Translational else float(np.deg2rad(value)), False)
    model.realizePosition(state)
    points.append([model.getBodySet().get(b).getPositionInGround(state).get(i)
                   for b in ['calcn_r', 'toes_r', 'calcn_l', 'toes_l'] for i in range(3)])
points = np.array(points)
with (OUT/'marker-proxies-m.trc').open('w') as f:
    f.write('PathFileType\t4\t(X/Y/Z)\tmarker-proxies-m.trc\n')
    f.write('DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n')
    f.write(f'60\t60\t{len(data)}\t4\tm\t60\t1\t{len(data)}\n')
    f.write('Frame#\tTime\tr_calc\t\t\tr_toe\t\t\tL_calc\t\t\tL_toe\t\t\n')
    f.write('\t\t'+'\t'.join(f'{axis}{j}' for j in range(1,5) for axis in 'XYZ')+'\n\n')
    for j,(row,p) in enumerate(zip(data,points),1):
        f.write('\t'.join(map(str,[j,row[0],*p]))+'\n')
gn, grf = read(ROOT/'ground_reaction_forces.mot')
renamed = [n.replace('force1_v', 'R_ground_force_v').replace('force1_p', 'R_ground_force_p').replace('force2_v', 'L_ground_force_v').replace('force2_p', 'L_ground_force_p').replace('torque1_', 'R_ground_torque_').replace('torque2_', 'L_ground_torque_') for n in gn]
with (OUT/'forces-renamed.mot').open('w') as f:
    f.write(f'nRows={len(grf)}\nnColumns={len(gn)}\nendheader\n'+'\t'.join(renamed)+'\n')
    np.savetxt(f,grf,delimiter='\t')
report = {'source_sha256': {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['motion.mot','model.osim','ground_reaction_forces.mot']},
          'motion_rows':len(data),'grf_rows':len(grf),'motion_time':[data[0,0],data[-1,0]],
          'markers':model.getMarkerSet().getSize(),'muscles':model.getMuscles().getSize(),'forces':model.getForceSet().getSize(),
          'marker_proxies_only':True,'marker_coordinates_unit':'m',
          'foot_proxy_length_m':{side:float(np.mean(np.linalg.norm(points[:,a:a+3]-points[:,b:b+3],axis=1))) for side,a,b in [('right',0,3),('left',6,9)]},
          'original_grf_columns':gn,'renamed_grf_columns':renamed}
(OUT/'preparation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
