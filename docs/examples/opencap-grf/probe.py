"""Isolated COP compatibility experiment; no production results or app changes."""
import argparse
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import numpy as np
import torch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--candidate', type=Path, required=True)
parser.add_argument('--inputs', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
OUT = args.output.resolve()
SOURCE = args.candidate.resolve()/'OpenSimPipeline/ForGaitDynamics/COP_Predictor.py'
WEIGHTS = SOURCE.with_name('cop_mlp_ar.pt')
spec = importlib.util.spec_from_file_location('cop_candidate', SOURCE)
cop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cop)
result = {'candidate_commit':'6369b19d9b53fa357befe651401af25b8350622d',
          'checkpoint_sha256':hashlib.sha256(WEIGHTS.read_bytes()).hexdigest(),
          'torch_version':torch.__version__, 'marker_proxies_only':True,
          'scientific_validation':False}
try:
    cop.load_cop_model(str(WEIGHTS))
    result['default_checkpoint_load'] = 'success'
except Exception as e:
    result['default_checkpoint_load'] = type(e).__name__
    result['default_load_detail'] = 'Default weights_only loading rejects NumPy constructors in this checkpoint.'

# Only permit the observed NumPy serialization constructors/dtypes.
allow = [np.core.multiarray._reconstruct,np.ndarray,np.dtype,
         type(np.dtype('float64')),type(np.dtype('float32'))]
baseline = cop.predict_cop_for_trial
assert inspect.getsource(baseline).count('/ 1_000_000.0') == 2
fixed_source = inspect.getsource(baseline).replace('/ 1_000_000.0', '/ 1000.0')
namespace = dict(cop.__dict__)
exec(fixed_source,namespace)
unit_diagnostic = namespace['predict_cop_for_trial']

with torch.serialization.safe_globals(allow):
    ckpt = torch.load(WEIGHTS,map_location='cpu',weights_only=True)
    result['checkpoint_training_foot_length_mean'] = float(ckpt['mu_X'][3])
    result['checkpoint_training_foot_length_std'] = float(ckpt['std_X'][3])
    for mode,fn in [('published_units',baseline),('metre_unit_diagnostic',unit_diagnostic)]:
        runs = []
        for shift in [0.0,0.25]:
            folder=OUT/f'{mode}-{shift}'
            folder.mkdir(exist_ok=False)
            grf=folder/'input-copy.mot'; trc=folder/'markers-copy.trc'; output=folder/'cop-experiment.mot'
            shutil.copy2(OUT/'forces-renamed.mot',grf)
            lines=(OUT/'marker-proxies-m.trc').read_text().splitlines()
            arr=np.loadtxt(lines[6:]); arr[:,2::3]+=shift
            with trc.open('w') as f:
                f.write('\n'.join(lines[:6])+'\n'); np.savetxt(f,arr,delimiter='\t')
            before=cop.load_mot_to_struct(grf)
            try:
                fn(str(grf),str(trc),trial_name='technical-probe',artifact_path=str(WEIGHTS),output_grf_path=str(output))
                after=cop.load_mot_to_struct(output)
                preserved=all(np.allclose(before[k],after[k],atol=1e-10,rtol=0) for k in before if k not in ['time','R_ground_force_px','L_ground_force_px'])
                runs.append(after)
                result.setdefault(mode,{})[str(shift)]={'rows':len(after['time']),
                    'finite':all(np.isfinite(v).all() for v in after.values()),
                    'input_copy_deleted':not grf.exists(),'non_px_force_point_torque_columns_preserved':preserved,
                    'cop_x_range_m':{side:[float(np.min(after[f'{side}_ground_force_px'])),float(np.max(after[f'{side}_ground_force_px']))] for side in ['R','L']}}
            except Exception as e:
                result.setdefault(mode,{})[str(shift)]={'error':type(e).__name__+': '+str(e)}
        if len(runs)==2:
            result[mode]['translation_response_m']={side:float(np.median(runs[1][f'{side}_ground_force_px']-runs[0][f'{side}_ground_force_px'])) for side in ['R','L']}
result['original_sources_preserved']=all(hashlib.sha256((args.inputs.resolve()/name).read_bytes()).hexdigest()==digest for name,digest in json.loads((OUT/'preparation.json').read_text())['source_sha256'].items())
(OUT/'compatibility.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
