"""Standalone inverse dynamics of the uploaded HU3D/GaitDynamics demonstration.

Requires Python 3.12, OpenSim 4.6, NumPy, SciPy and matplotlib.
The application is not used. See README.md for assumptions and interpretation.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform
import os
import xml.etree.ElementTree as ET

import numpy as np
import scipy
from scipy.interpolate import make_interp_spline
from scipy.signal import butter, sosfiltfilt
import opensim as osim
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


DEPENDENT = {'knee_angle_r_beta': 'knee_angle_r', 'knee_angle_l_beta': 'knee_angle_l'}
ROOT = ['pelvis_tilt', 'pelvis_list', 'pelvis_rotation', 'pelvis_tx', 'pelvis_ty', 'pelvis_tz']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_mot(path):
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    end = next(i for i, line in enumerate(lines) if line.strip() == 'endheader')
    header = dict(line.split('=', 1) for line in lines[:end] if '=' in line)
    names = lines[end + 1].split()
    values = np.loadtxt(lines[end + 2:])
    require(values.ndim == 2 and values.shape[1] == len(names), 'Invalid MOT dimensions')
    require(np.isfinite(values).all() and np.all(np.diff(values[:, 0]) > 0), 'Invalid MOT samples')
    require(len(set(names)) == len(names), 'Duplicate MOT columns')
    require(int(header['nRows']) == len(values), 'Wrong MOT row count')
    return header, names, values


def csv_write(path, names, values):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(names)
        w.writerows([[format(float(v), '.12g') for v in row] for row in values])


def mot_write(path, names, values, degrees=False):
    with path.open('w', encoding='utf-8') as f:
        f.write(f'HU3D standalone inverse dynamics\nversion=1\nnRows={len(values)}\nnColumns={len(names)}\ninDegrees={"yes" if degrees else "no"}\nendheader\n')
        f.write('\t'.join(names) + '\n')
        np.savetxt(f, values, delimiter='\t', fmt='%.17g')


def vec(v):
    return np.array([v.get(i) for i in range(3)])


def mat(r):
    return np.array([[r.get(i, j) for j in range(3)] for i in range(3)])


def inertia_matrix(body):
    inertia = body.getInertia()
    moments = vec(inertia.getMoments()); products = vec(inertia.getProducts())
    return np.array([[moments[0], products[0], products[1]],
                     [products[0], moments[1], products[2]],
                     [products[1], products[2], moments[2]]])


def lowpass(data, hz, fs):
    # Explicit zero-phase, fourth-order forward/backward Butterworth.
    return sosfiltfilt(butter(4, hz, fs=fs, output='sos'), data, axis=0, padtype='odd')


def verify_models(a, b):
    result = []
    require(a.getBodySet().getSize() == b.getBodySet().getSize(), 'Models differ in body count')
    for body in a.getBodySet():
        other = b.getBodySet().get(body.getName())
        mass = body.getMass(); inertia = inertia_matrix(body); com = vec(body.getMassCenter())
        require(mass > 0 and np.linalg.eigvalsh(inertia).min() > 0, 'Invalid inertial parameters')
        eig = np.linalg.eigvalsh(inertia)
        triangle_valid = bool(eig[-1] <= eig[0] + eig[1] + 1e-9)
        require(abs(mass-other.getMass()) < 1e-10 and np.allclose(inertia, inertia_matrix(other), atol=1e-12, rtol=0) and np.allclose(com, vec(other.getMassCenter()), atol=1e-12, rtol=0), 'Original and numerical inertial properties differ')
        result.append({'body': body.getName(), 'mass_kg': mass, 'center_of_mass_m': com.tolist(), 'inertia_kg_m2': inertia.tolist(), 'inertia_triangle_inequality_valid': triangle_valid})
    require(np.allclose(vec(a.getGravity()), vec(b.getGravity()), atol=1e-12, rtol=0), 'Different gravity')
    return result


def prepare_model(model_path, wrench_path):
    model = osim.Model(str(model_path))
    require(model.getForceSet().getSize() == 0, 'Expected force-free numerical model: would otherwise double count forces')
    data = osim.Storage(str(wrench_path))  # keep Storage alive for the ExternalForce references
    for i, side in enumerate(('r', 'l'), 1):
        force = osim.ExternalForce()
        force.setName('predicted_grf_' + side)
        force.setAppliedToBodyName('calcn_' + side)
        force.setForceExpressedInBodyName('ground')
        force.setPointExpressedInBodyName('ground')
        force.setForceIdentifier(f'foot{i}_force_v')
        force.setPointIdentifier(f'foot{i}_point_p')
        force.setTorqueIdentifier(f'foot{i}_moment_m')
        force.setDataSource(data)
        model.addForce(force)
    model.finalizeConnections()
    state = model.initSystem()
    # Discover mobility ordering from the actual bindings, not CoordinateSet ordering.
    index = {}
    for c in model.getCoordinateSet():
        state.updU().setToZero(); c.setSpeedValue(state, 1)
        nonzero = [i for i in range(state.getNU()) if abs(state.getU().get(i)) > .5]
        require(len(nonzero) == 1, 'Unsupported coordinate-to-speed map')
        index[c.getName()] = nonzero[0]
    state.updU().setToZero()
    return model, state, index, data


def set_state(model, state, names, q, u, a, time, index):
    state.setTime(float(time))
    for name, value, speed in zip(names, q, u):
        c = model.getCoordinateSet().get(name)
        c.setValue(state, float(value), False)
        c.setSpeedValue(state, float(speed))
    udot = osim.Vector(state.getNU(), 0.)
    for name, value in zip(names, a):
        udot.set(index[name], float(value))
    model.realizeVelocity(state)
    # Custom joints here use qdot=u; fail on other mobilizers instead of silently misusing them.
    require(np.allclose([state.getQDot().get(i) for i in range(state.getNQ())], [state.getU().get(i) for i in range(state.getNU())], atol=1e-10), 'This script requires qdot=u')
    return udot


def root_wrench_and_balance(model, state, udot, tau, index, foot_f, foot_m):
    root_body = model.getBodySet().get('pelvis')
    root_point = vec(root_body.getPositionInGround(state))
    # Power-equivalent conversion: B.T * spatial_wrench = root generalized forces.
    old_u = [state.getU().get(i) for i in range(state.getNU())]
    B = np.zeros((6, 6))
    for j, name in enumerate(ROOT):
        state.updU().setToZero(); model.getCoordinateSet().get(name).setSpeedValue(state, 1.)
        model.realizeVelocity(state)
        velocity = root_body.getVelocityInGround(state)
        B[:, j] = np.r_[vec(velocity.get(0)), vec(velocity.get(1))]
    for i, value in enumerate(old_u):
        state.updU().set(i, value)
    model.realizeVelocity(state)
    wrench = np.linalg.solve(B.T, [tau[index[name]] for name in ROOT])
    # Independent whole-body Newton-Euler balance with prescribed accelerations.
    acc = osim.VectorOfSpatialVec()
    model.getMatterSubsystem().calcBodyAccelerationFromUDot(state, udot, acc)
    total_f = np.zeros(3); total_m_origin = np.zeros(3)
    for body in model.getBodySet():
        transform = body.getTransformInGround(state); R = mat(transform.R())
        offset = R @ vec(body.getMassCenter()); point = vec(transform.p()) + offset
        body_a = acc.get(int(body.getMobilizedBodyIndex()))
        alpha = vec(body_a.get(0)); omega = vec(body.getAngularVelocityInGround(state))
        com_a = vec(body_a.get(1)) + np.cross(alpha, offset) + np.cross(omega, np.cross(omega, offset))
        inertia = R @ inertia_matrix(body) @ R.T
        f = body.getMass() * (com_a - vec(model.getGravity()))
        total_f += f
        total_m_origin += inertia @ alpha + np.cross(omega, inertia @ omega) + np.cross(point, f)
    expected_f = total_f - foot_f.sum(axis=0)
    expected_m_origin = total_m_origin - foot_m.sum(axis=0)
    expected = np.r_[expected_m_origin - np.cross(root_point, expected_f), expected_f]
    return wrench, float(np.max(np.abs(wrench - expected))), root_point


def stats(values, mask):
    v = values[mask]
    return {'rms': float(np.sqrt(np.mean(v*v))), 'max_abs': float(np.max(np.abs(v))), 'mean': float(np.mean(v))}


def plot_moments(output, times, cases, cutoff):
    pairs = [('hip_flexion', 'Heup · flexiecoördinaat'), ('hip_adduction', 'Heup · adductiecoördinaat'), ('hip_rotation', 'Heup · rotatiecoördinaat'), ('knee_angle', 'Knie · flexiecoördinaat'), ('ankle_angle', 'Enkel · flexiecoördinaat'), ('subtalar_angle', 'Subtalaire coördinaat'), ('mtp_angle', 'Teen · MTP-coördinaat')]
    fig, axes = plt.subplots(4, 2, figsize=(14, 13), sharex=True)
    central = cases[cutoff]; colors = ('#0072B2', '#D55E00')
    for ax, (name, label) in zip(axes.flat, pairs):
        for side, color in zip(('r', 'l'), colors):
            j = central['names'].index(name+'_'+side)
            lo = np.min([c['independent'][:, j] for c in cases.values()], axis=0)
            hi = np.max([c['independent'][:, j] for c in cases.values()], axis=0)
            ax.fill_between(times, lo, hi, color=color, alpha=.12)
            ax.plot(times, central['independent'][:, j], color=color, lw=1.8, linestyle='-' if side=='r' else '--', label='Rechts' if side=='r' else 'Links')
        decorate(ax, times); ax.set_title(label, loc='left'); ax.set_ylabel('Nm')
    ax=axes.flat[-1]
    for j, label, color in zip(('lumbar_extension', 'lumbar_bending', 'lumbar_rotation'), ('Extensie', 'Zijwaartse buiging', 'Rotatie'), ('#0072B2', '#D55E00', '#009E73')):
        ax.plot(times, central['independent'][:, central['names'].index(j)], label=label, color=color)
    decorate(ax,times);ax.set_title('Romp · lumbale coördinaten',loc='left');ax.set_ylabel('Nm');ax.legend(fontsize=9)
    axes[0,0].legend(frameon=False)
    for ax in axes[-1]:ax.set_xlabel('Tijd (s)')
    fig.suptitle('Inverse dynamica · netto momenten per modelcoördinaat',x=.075,ha='left',fontsize=19,fontweight='bold')
    fig.text(.075,.946,f'Voorspelde grondreactiekrachten + massa, traagheid en zwaartekracht · hoofdcurve {cutoff:g} Hz',fontsize=11)
    fig.text(.075,.927,'Eenbenige evenwichtsoefening: GaitDynamics-krachten niet gevalideerd voor deze beweging.',fontsize=10,color='#9A3412')
    fig.text(.075,.022,'Schaduw: gevoeligheid bij 4, 6 en 8 Hz, geen betrouwbaarheidsinterval. Grijze randen: eerste/laatste 0,15 s.',fontsize=10)
    fig.text(.075,.009,'Tekens: benodigde interne actuatormomenten in positieve modelrichting. Geen 3D-gewrichtsreactiemomenten of knie-adductiemoment.',fontsize=10)
    fig.subplots_adjust(left=.075,right=.98,bottom=.075,top=.90,hspace=.42,wspace=.22)
    for ext in ('png','pdf'):fig.savefig(output/f'inverse-dynamica-momenten.{ext}',dpi=180)
    plt.close(fig)


def decorate(ax,times):
    ax.axhline(0,color='#526071',lw=.7,alpha=.5);ax.grid(alpha=.2);ax.set_xlim(times[0],times[-1])
    ax.axvspan(times[0],times[0]+.15,color='grey',alpha=.12);ax.axvspan(times[-1]-.15,times[-1],color='grey',alpha=.12)


def plot_residual(output,times,cases,cutoff,mass,g):
    fig,axes=plt.subplots(2,3,figsize=(15,8),sharex=True)
    for row in range(2):
        for j in range(3):
            k=3+j if row==0 else j;ax=axes[row,j]
            for hz,c in cases.items():ax.plot(times,c['wrench'][:,k],label=f'{hz:g} Hz',lw=2 if hz==cutoff else 1,alpha=1 if hz==cutoff else .55)
            decorate(ax,times);ax.set_title(('Restkracht · ' if row==0 else 'Restmoment bij bekken · ')+'XYZ'[j],loc='left');ax.set_ylabel('N' if row==0 else 'Nm')
            if row==1:ax.set_xlabel('Tijd (s)')
    axes[0,0].legend(frameon=False)
    fig.suptitle('Controle · ontbrekende krachten en momenten',x=.07,ha='left',fontsize=19,fontweight='bold')
    fig.text(.07,.925,f'Globale grondassen · verticale as Y · modelmassa {mass:g} kg · lichaamsgewicht {mass*g:.1f} N',fontsize=11)
    fig.text(.07,.035,'Dit zijn de restbelastingen die nodig zijn om de ingevoerde beweging te laten kloppen met de voorspelde voetkrachten.',fontsize=10)
    fig.text(.07,.017,'Ze zijn niet weggewerkt. Grote restbelastingen beperken de biomechanische interpretatie van de gewrichtsmomenten.',fontsize=10)
    fig.subplots_adjust(left=.07,right=.98,bottom=.14,top=.85,hspace=.4,wspace=.27)
    for ext in ('png','pdf'):fig.savefig(output/f'inverse-dynamica-restbelastingen.{ext}',dpi=180)
    plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('motion','forces','model','numerical-model','output'):p.add_argument('--'+arg,type=Path,required=True)
    p.add_argument('--cutoff',type=float,default=6.)
    args=p.parse_args();output=args.output.resolve()
    require(not output.exists(),'Use a new output directory: existing results are never overwritten')
    output.mkdir(parents=True)
    args.motion=args.motion.resolve();args.forces=args.forces.resolve();args.model=args.model.resolve();args.numerical_model=args.numerical_model.resolve()
    inputs=[args.motion,args.forces,args.model,args.numerical_model];hashes={x.name:sha(x) for x in inputs}
    mh,mnames,motion=read_mot(args.motion);_,fnames,forces=read_mot(args.forces)
    original_tree=ET.parse(args.model)
    for e in original_tree.findall('.//attached_geometry'):e.clear()
    check_path=output/'model-controle-zonder-meshes.osim';original_tree.write(check_path,encoding='utf-8',xml_declaration=True)
    numerical=osim.Model(str(args.numerical_model));ns=numerical.initSystem();original=osim.Model(str(check_path));original.initSystem()
    bodies=verify_models(original,numerical); mass=numerical.getTotalMass(ns);gravity=vec(numerical.getGravity());g=np.linalg.norm(gravity)
    require(abs(mass-70.)<1e-8,'This demonstration was predicted using mass=70 kg; use matching force/model mass')
    require(numerical.getForceSet().getSize()==0,'Numerical model must contain no other forces')
    # Validate the actual supplied couplers before applying the reduced-coordinate projection.
    couplers=ET.parse(args.numerical_model).findall('.//CoordinateCouplerConstraint')
    require(len(couplers)==2,'Unexpected constraint count')
    for c in couplers:
        dep=c.findtext('dependent_coordinate_name');ind=c.findtext('independent_coordinate_names')
        require(DEPENDENT.get(dep)==ind and c.findtext('isEnforced')=='true' and np.allclose(np.fromstring(c.findtext('coupled_coordinates_function/LinearFunction/coefficients'),sep=' '),[1,0]) and float(c.findtext('scale_factor'))==1.,'Only validated linear knee beta couplers are supported')
    static_udot = osim.Vector(ns.getNU(), 0.)
    static_tau = osim.InverseDynamicsSolver(numerical).solve(ns, static_udot).to_numpy().copy()
    static_u_index = {}
    for c in numerical.getCoordinateSet():
        ns.updU().setToZero(); c.setSpeedValue(ns, 1.)
        static_u_index[c.getName()] = next(i for i in range(ns.getNU()) if abs(ns.getU().get(i)) > .5)
    ns.updU().setToZero(); numerical.realizeVelocity(ns)
    static_root_force = np.array([static_tau[static_u_index[n]] for n in ROOT[3:]])
    require(np.max(abs(static_root_force + mass*gravity)) < 1e-8, 'Static support does not equal body weight')
    zero_gravity_model = osim.Model(str(args.numerical_model)); zero_gravity_model.setGravity(osim.Vec3(0.,0.,0.))
    zero_state = zero_gravity_model.initSystem()
    zero_tau = osim.InverseDynamicsSolver(zero_gravity_model).solve(zero_state, osim.Vector(zero_state.getNU(),0.)).to_numpy().copy()
    require(np.max(abs(zero_tau)) < 1e-10, 'Static zero-gravity control should require no effort')
    controls = {'static_support_force_ground_N':static_root_force.tolist(),
                'static_support_minus_weight_max_error_N':float(np.max(abs(static_root_force+mass*gravity))),
                'static_zero_gravity_max_effort_N_or_Nm':float(np.max(abs(zero_tau)))}
    times=motion[0,0]+np.arange(len(forces))/100.
    require(np.max(np.abs(times-forces[:,0]))<=5.01e-6,'Force times must match the 100 Hz inference grid')
    dt=np.median(np.diff(motion[:,0]));fs=1/dt
    require(np.max(abs(np.diff(motion[:,0])-dt))<1e-7,'Uniform motion sampling required')
    require(times[0]>=motion[0,0] and times[-1]<=motion[-1,0],'Extrapolation is forbidden')
    independent_names=mnames[1:];require(len(independent_names)==23,'Expected converted 23-coordinate motion')
    full_names=[c.getName() for c in numerical.getCoordinateSet()]
    require(set(full_names)==set(independent_names)|set(DEPENDENT),'Motion/model coordinate mismatch')
    q=motion[:,1:].copy();require(mh.get('inDegrees') in ('yes','no'),'Motion must declare angular units')
    for j,name in enumerate(independent_names):
        if numerical.getCoordinateSet().get(name).getMotionType()==osim.Coordinate.Rotational:
            if mh['inDegrees']=='yes':q[:,j]=np.deg2rad(q[:,j])
            q[:,j]=np.unwrap(q[:,j])
    force_columns=dict(zip(fnames,forces.T));raw_f=[];raw_m=[];invalid=[]
    for side in (1,2):
        f=np.column_stack([force_columns[f'force{side}_v{x}'] for x in 'xyz'])
        cop=np.column_stack([force_columns[f'force{side}_p{x}'] for x in 'xyz'])
        torque=np.column_stack([force_columns[f'torque{side}_{x}'] for x in 'xyz'])
        require(np.count_nonzero(torque)==0,'This demonstration assumes unknown zero-placeholder free moments')
        valid=f[:,1]>=20.;invalid.append(int((~valid).sum()))
        f[~valid]=0.;raw_f.append(f);raw_m.append(np.cross(cop,f))
    raw_f=np.stack(raw_f,axis=1);raw_m=np.stack(raw_m,axis=1)
    interior=(times>=times[0]+.15)&(times<=times[-1]-.15)
    require(args.cutoff in (4.,6.,8.),'Main cutoff must be 4, 6 or 8 Hz')
    cases={};mechanics={};kinematic_changes={}
    tool_checks = {}
    for hz in (4.,6.,8.):
        folder=output/f'{hz:g}Hz';folder.mkdir()
        filtered_q=lowpass(q,hz,fs);spline=make_interp_spline(motion[:,0],filtered_q,k=5,axis=0)
        qi=spline(times);ui=spline(times,nu=1);ai=spline(times,nu=2)
        fullq=np.column_stack([qi[:,independent_names.index(DEPENDENT.get(n,n))] for n in full_names])
        fullu=np.column_stack([ui[:,independent_names.index(DEPENDENT.get(n,n))] for n in full_names])
        fulla=np.column_stack([ai[:,independent_names.index(DEPENDENT.get(n,n))] for n in full_names])
        F=lowpass(raw_f,hz,100.);M=lowpass(raw_m,hz,100.)
        # Store equivalent global-origin wrenches, NOT independently filtered CoPs.
        wn=['time'];wv=[times]
        for i in range(2):
            wn += [f'foot{i+1}_force_v{x}' for x in 'xyz']+[f'foot{i+1}_point_p{x}' for x in 'xyz']+[f'foot{i+1}_moment_m{x}' for x in 'xyz']
            wv += [F[:,i,j] for j in range(3)]+[np.zeros(len(times)) for _ in range(3)]+[M[:,i,j] for j in range(3)]
        wrench_path=folder/'externe-belastingen.mot';mot_write(wrench_path,wn,np.column_stack(wv))
        model,state,index,load_storage=prepare_model(args.numerical_model,wrench_path);solver=osim.InverseDynamicsSolver(model)
        tau=[];wrenches=[];errors=[]
        for row,t in enumerate(times):
            udot=set_state(model,state,full_names,fullq[row],fullu[row],fulla[row],t,index)
            tau_row=np.array([v for v in solver.solve(state,udot).to_numpy()])
            w,e,point=root_wrench_and_balance(model,state,udot,tau_row,index,F[row],M[row])
            tau.append(tau_row);wrenches.append(w);errors.append(e)
        tau=np.array(tau);wrenches=np.array(wrenches)
        reduced=np.column_stack([tau[:,index[n]]+(tau[:,index[n+'_beta']] if n.startswith('knee_angle_') else 0.) for n in independent_names])
        full_in_coord_order=np.column_stack([tau[:,index[n]] for n in full_names])
        power_full=np.sum(full_in_coord_order*fullu,axis=1);power_reduced=np.sum(reduced*ui,axis=1)
        require(np.max(abs(power_full-power_reduced))<1e-8,'Reduced knee moments do not preserve power')
        require(max(errors)<1e-6,'Whole-body Newton-Euler residual cross-check failed')
        cases[hz]={'names':independent_names,'independent':reduced,'wrench':wrenches}
        mechanics[str(hz)]={'newton_euler_max_abs_error_N_or_Nm':max(errors),'constraint_projection_power_max_abs_error_W':float(np.max(abs(power_full-power_reduced)))}
        moment_names=[n+('_force_N' if n in ROOT[3:] else '_moment_Nm') for n in independent_names]
        csv_write(folder/'inverse-dynamica.csv',['time_s']+moment_names,np.column_stack([times,reduced]))
        mot_write(folder/'inverse-dynamica.sto',['time']+[n+('_force' if n in ROOT[3:] else '_moment') for n in independent_names],np.column_stack([times,reduced]))
        csv_write(folder/'inverse-dynamica-volledig.csv',['time_s']+[n+('_force_N' if n in ROOT[3:] else '_moment_Nm') for n in full_names],np.column_stack([times,full_in_coord_order]))
        csv_write(folder/'restbelastingen.csv',['time_s','Mx_ground_Nm','My_ground_Nm','Mz_ground_Nm','Fx_ground_N','Fy_ground_N','Fz_ground_N'],np.column_stack([times,wrenches]))
        mot_write(folder/'beweging-gefilterd.mot',['time']+full_names,np.column_stack([times,fullq]))
        csv_write(folder/'snelheden.csv',['time_s']+[n+('_m_per_s' if n in ROOT[3:] else '_rad_per_s') for n in full_names],np.column_stack([times,fullu]))
        csv_write(folder/'versnellingen.csv',['time_s']+[n+('_m_per_s2' if n in ROOT[3:] else '_rad_per_s2') for n in full_names],np.column_stack([times,fulla]))
        kinematic_changes[str(hz)]={n:float(np.sqrt(np.mean((filtered_q[:,j]-q[:,j])**2)))*(180/np.pi if n not in ROOT[3:] else 1.) for j,n in enumerate(independent_names)}
        # OpenSim GUI/CLI-compatible loads and tool setup, also used for independent rerun below.
        ext=osim.ExternalLoads();ext.setDataFileName('externe-belastingen.mot')
        for i,side in enumerate(('r','l'),1):
            ef=osim.ExternalForce();ef.setName('predicted_grf_'+side);ef.setAppliedToBodyName('calcn_'+side);ef.setForceExpressedInBodyName('ground');ef.setPointExpressedInBodyName('ground');ef.setForceIdentifier(f'foot{i}_force_v');ef.setPointIdentifier(f'foot{i}_point_p');ef.setTorqueIdentifier(f'foot{i}_moment_m');ext.cloneAndAppend(ef)
        ext.printToXML(str(folder/'ExternalLoads.xml'))
        clean=osim.Model(str(args.numerical_model));clean.printToXML(str(folder/'model.osim'))
        tool=osim.InverseDynamicsTool();tool.setName(f'HU3D_ID_{hz:g}Hz');tool.setModelFileName('model.osim');tool.setCoordinatesFileName('beweging-gefilterd.mot');tool.setExternalLoadsFileName('ExternalLoads.xml');tool.setLowpassCutoffFrequency(-1);tool.setStartTime(float(times[0]));tool.setEndTime(float(times[-1]));tool.setResultsDir('.');tool.setOutputGenForceFileName('opensim-tool-volledig.sto');tool.printToXML(str(folder/'InverseDynamicsSetup.xml'))
        # Independent standard-tool rerun: its own GCV splines differentiate the exported 100 Hz table.
        cwd = Path.cwd()
        try:
            os.chdir(folder)
            osim.Logger.removeFileSink()
            osim.Logger.addFileSink(str(folder/'opensim-tool.log'))
            rerun = osim.InverseDynamicsTool('InverseDynamicsSetup.xml')
            require(rerun.run(), 'Standard OpenSim InverseDynamicsTool failed')
        finally:
            osim.Logger.removeFileSink()
            os.chdir(cwd)
        _, tool_names, tool_data = read_mot(folder/'opensim-tool-volledig.sto')
        require(np.allclose(tool_data[:,0],times,atol=1e-8,rtol=0), 'Standard-tool times differ')
        checks = {}
        for j, name in enumerate(tool_names[1:],1):
            coord = name.rsplit('_',1)[0]
            delta = tool_data[:,j] - tau[:,index[coord]]
            checks[coord] = {'interior_rms_difference_N_or_Nm':float(np.sqrt(np.mean(delta[interior]**2))),
                            'interior_max_abs_difference_N_or_Nm':float(np.max(abs(delta[interior]))),
                            'all_samples_max_abs_difference_N_or_Nm':float(np.max(abs(delta)))}
            limit = 1. if coord in ROOT[3:] else .1
            require(checks[coord]['interior_max_abs_difference_N_or_Nm'] < limit, 'Standard-tool interior validation differs beyond tolerance')
        tool_checks[str(hz)] = checks

    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42})
    plot_moments(output,times,cases,args.cutoff);plot_residual(output,times,cases,args.cutoff,mass,g)
    sensitivity={}
    for hz,c in cases.items():
        force_norm=np.linalg.norm(c['wrench'][:,3:],axis=1);moment_norm=np.linalg.norm(c['wrench'][:,:3],axis=1)
        sensitivity[str(hz)]={'residual_force_norm_N':stats(force_norm,interior),'residual_force_rms_percent_BW':100*stats(force_norm,interior)['rms']/(mass*g),'residual_moment_norm_Nm':stats(moment_norm,interior),'moment_components':{n:stats(c['independent'][:,j],interior) for j,n in enumerate(independent_names) if n not in ROOT},'RMS_difference_from_main_N_or_Nm':{n:float(np.sqrt(np.mean((c['independent'][interior,j]-cases[args.cutoff]['independent'][interior,j])**2))) for j,n in enumerate(independent_names)}}
    report={'method':'OpenSim InverseDynamicsSolver, full prescribed coordinates and analytical spline derivatives; reduced forces J.T*tau', 'application_modified':False,'versions':{'python':platform.python_version(),'opensim':osim.GetVersionAndDate(),'numpy':np.__version__,'scipy':scipy.__version__,'matplotlib':matplotlib.__version__},'input_sha256':hashes,'mass_kg':mass,'result_status':'diagnostic_only_non_gait_activity_force_prediction_not_validated','movement_context':{'activity': 'single_leg_balance_exercise', 'source': 'user clarification', 'stance_foot': 'not yet established', 'other_contacts': 'not yet established', 'gaitdynamics_force_accuracy_validated_for_activity': False},'gravity_m_per_s2':gravity.tolist(),'body_mass_positive_and_inertia_positive_definite':True,'inertia_triangle_inequality_violations':[b['body'] for b in bodies if not b['inertia_triangle_inequality_valid']],'original_and_numerical_inertial_parameters_identical':True,'bodies':bodies,'force_model_mass_basis_kg':70,'motion_rows':len(motion),'force_rows':len(forces),'motion_rate_hz':fs,'analysis_rate_hz':100,'time_range_s':[float(times[0]),float(times[-1])],'timestamp_max_rounding_s':float(np.max(abs(times-forces[:,0]))),'filter':{'type':'4th order Butterworth SOS, forward-backward zero phase, odd padding','main_cutoff_Hz':args.cutoff,'sensitivity_cutoffs_Hz':[4,6,8],'kinematics':'filter unwrapped radians/metres on original 60 Hz grid; interpolate quintic spline; differentiate analytically','loads':'zero raw foot force below 20 N Fy; form global-origin moment CoP cross F; filter F and M together at 100 Hz','free_contact_torque':'unknown; zero-placeholder assumption, no recovered free moment'},'force_suppressed_samples_right_left':invalid,'edge_exclusion_s':.15,'statistics_time_range_s':[float(times[interior][0]),float(times[interior][-1])],'foot_mapping':{'force1':'calcn_r','force2':'calcn_l'},'coupler_projection':{'knee_angle_r':'tau_knee_angle_r + tau_knee_angle_r_beta','knee_angle_l':'tau_knee_angle_l + tau_knee_angle_l_beta'},'signs':'required internal generalized actuator force/moment, positive model coordinate; no side mirroring','residual_wrench':'global XYZ; moment about instantaneous pelvis origin, obtained by virtual-power spatial transformation','validation':mechanics,'static_controls':controls,'standard_tool_comparison':tool_checks,'filter_changes_RMS_deg_or_m':kinematic_changes,'sensitivity_and_statistics':sensitivity,'limitations':['User identifies this motion as a single-leg balance exercise, not walking/running; GaitDynamics force accuracy is not established for this activity.','Predicted rather than measured GRFs and CoPs; no free contact moments.','Segment masses and inertia matrices are present and positive definite, but toes_r and toes_l violate the rigid-body inertia triangle inequality; original values retained. Participant-specific inertial accuracy is not established.','Upper-body segments, head and arms were merged into a rigid torso in the existing conversion; neutral-pose inertia does not preserve their independent dynamics.','No motion/force optimization, no residual reduction, no muscle force allocation.','Coordinate torques are not 3-axis joint reaction moments; knee adduction is not reported.','Short 1.58-second demo; endpoint derivatives and filter sensitivity limit interpretation.','Force assignment to calcn is a whole-foot wrench assumption; toe/subtalar load partition is unvalidated.']}
    require(all(sha(x)==hashes[x.name] for x in inputs),'An input file was changed')
    (output/'berekeningsrapport.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'mass_kg':mass,'validation':mechanics,'main_residuals':{k:v for k,v in sensitivity[str(args.cutoff)].items() if k.startswith('residual')}},indent=2))


if __name__=='__main__':main()
