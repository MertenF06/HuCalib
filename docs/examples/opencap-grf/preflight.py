"""Test the upstream contact-generation function on disposable model copies."""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import traceback
import numpy as np
import opensim

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--candidate', type=Path, required=True)
parser.add_argument('--inputs', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
OUT,ROOT,CANDIDATE=args.output.resolve(),args.inputs.resolve(),args.candidate.resolve()
OUT.mkdir(parents=True, exist_ok=True)
model=opensim.Model(str(ROOT/'model.osim')); model.initSystem()
coordinates=[model.getCoordinateSet().get(i).getName() for i in range(model.getCoordinateSet().getSize())]
spec=importlib.util.spec_from_file_location('settings_candidate',CANDIDATE/'UtilsDynamicSimulations/OpenSimAD/settingsOpenSimAD.py')
settings=importlib.util.module_from_spec(spec); spec.loader.exec_module(settings)
setup=settings.get_setup('walking_grf')
report={'source_coordinates':coordinates,'walking_tracking_coordinates_missing':sorted(set(setup.get('coordinates_toTrack',{}))-set(coordinates)),
        'simulation_executed':False,'contact_function_test':'not_started',
        'runtime_modules_available':{n:bool(importlib.util.find_spec(n)) for n in ['casadi','seaborn','yaml','pandas','scipy','requests']}}
source=ast.parse((CANDIDATE/'utilsProcessing.py').read_text())
fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='generate_model_with_contacts')
isolated=ast.Module(body=[fn],type_ignores=[])
namespace={'os':os,'np':np,'opensim':opensim}
exec(compile(isolated,'candidate_contact_function','exec'),namespace)
folder=OUT/'contact-preparation'/'session'/'OpenSimData'/'Model';folder.mkdir(parents=True,exist_ok=False)
shutil.copy2(ROOT/'model.osim',folder/'HU3D_scaled_adjusted.osim')
try:
    namespace['generate_model_with_contacts'](1e6,str(OUT/'contact-preparation'),'session',OpenSimModel='HU3D',contact_side='all')
    prepared=opensim.Model(str(folder/'HU3D_scaled_adjusted_contacts.osim'));prepared.initSystem()
    report['contact_function_test']='success'
    report['contact_forces_added']=prepared.getForceSet().getSize()
except Exception as e:
    report['contact_function_test']='failed'
    report['contact_error']=str(e)
    report['contact_traceback']=traceback.format_exc()
(OUT/'simulation-preflight.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
