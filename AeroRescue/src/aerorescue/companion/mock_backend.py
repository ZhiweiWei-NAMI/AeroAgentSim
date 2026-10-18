"""Explicit deterministic software model behind the existing adapter RPC contract.

No PX4, Gazebo, ns-3, socket, or native success is represented by this module.
The real kernel grants advances; agents cannot call this object or read its scene.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import random
import zlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from aerokernel.values import ResourceBudget

from .geometry import quaternion_matrix

NS = 1_000_000_000


class MockWorld:
    def __init__(self, scene: dict[str, Any], run_id: str, root: Path, seed: int):
        self.scene, self.run_id, self.root = scene, run_id, root
        self.config = scene.get('mock_backend', {})
        self.epoch = 'mock:' + run_id
        self.rng = random.Random(seed)
        self.now = 0
        self.vehicles: dict[str, dict[str, Any]] = {}
        self.commands: dict[str, dict[str, Any]] = {}
        self.frames: dict[tuple[str, str], tuple[dict, dict]] = {}
        self.payload = None
        self.radio_now = 0
        self.packets: list[dict[str, Any]] = []
        self.nodes: dict[str, list[float]] = {}
        self.closed = False
        root.mkdir(parents=True, exist_ok=True)
        (root / 'execution-backend.json').write_text(json.dumps({
            'execution_backend': 'mock', 'native': False,
            'model': 'bounded-acceleration point vehicle, gravity parcel, geometric RGB-D, delayed lossy packets',
            'seed': seed, 'clock_epoch': self.epoch,
            'limitations': ['not PX4 dynamics', 'not Gazebo contact', 'not ns-3 RF',
                            'camera-facing sprite rendering, not native human mesh'],
        }, indent=2))

    def log(self, kind, **fields):
        with (self.root / 'mock-world.jsonl').open('a') as out:
            out.write(json.dumps({'execution_backend': 'mock', 'kind': kind, 'sim_ns': self.now, **fields}) + '\n')

    def reset_flight(self, payload):
        self.now = 0
        for v in payload['vehicles']:
            self.vehicles[v['id']] = {'position': np.array(v['spawn'][:3], float),
                'velocity': np.zeros(3), 'home_z': float(v['spawn'][2]),
                'yaw': float(v['spawn'][5]), 'armed': False, 'mode': 'HOLD',
                'landed': 'ON_GROUND', 'energy_used_j': 0.0}
        if payload.get('payload'):
            self.payload = {'status': 'attached', 'released': False, 'source': 'mock_detachable_joint',
                'package_id': payload['payload']['package_id'], 'carrier': payload['payload']['carrier'],
                'source_time_ns': 0, 'observed_sim_ns': 0, 'execution_backend': 'mock'}
        return {**self.flight_result([], [], []), 'physics_step_ns': 4_000_000,
                'warmup_sim_ns': payload['warmup'], 'vehicles': list(self.vehicles)}

    def estimate(self, uid):
        v = self.vehicles[uid]
        return {'status': 'available', 'source': 'mock_kinematic_model', 'execution_backend': 'mock',
            'position_enu_m': v['position'].tolist(), 'velocity_enu_m_s': v['velocity'].tolist(),
            'attitude_body_frd_to_world_enu_xyzw': [math.cos(v['yaw']/2), math.sin(v['yaw']/2), 0.0, 0.0],
            'source_sim_ns': self.now, 'available_sim_ns': self.now, 'source_clock': 'mock_episode',
            'clock_epoch': self.epoch, 'origin_version': 'mock-world-origin',
            'clock_map': {'method': 'same explicit mock clock', 'uncertainty_ns': 0},
            'source_messages': ['mock_kinematic_state']}

    def flight_result(self, updates, events, receipts):
        samples = []
        for uid, v in self.vehicles.items():
            capacity = self.scene['vehicles'][uid].get('capacity_j', 240000)
            samples.append({'vehicle': uid, 'sim_ns': self.now, 'position_enu': v['position'].tolist(),
                'velocity_enu': v['velocity'].tolist(), 'attitude_quat': [0.0, 0.0, 0.0, 1.0],
                'battery_source': 'mock_energy_model',
                'battery': {'remaining_fraction': max(0., 1-v['energy_used_j']/capacity), 'voltage_v': 16.0},
                'armed': v['armed'], 'flight_mode': v['mode'], 'landed_state': v['landed'],
                'freshness': {'mavsdk_source_sim_ns': None}, 'vehicle_estimate': self.estimate(uid)})
        return {'reached_sim_ns': self.now, 'telemetry': samples, 'contacts': [],
                'command_updates': updates, 'payload_events': events, 'receiver_receipts': receipts}

    def command(self, payload):
        uid, cid, action = payload['vehicle'], payload['command_id'], payload['action']
        if cid in self.commands:
            return {'status': 'rejected', 'reason': 'command_id already used', 'command_id': cid}
        if any(c['vehicle'] == uid and c['status'] not in ('succeeded', 'failed', 'canceled') for c in self.commands.values()):
            return {'status': 'rejected', 'reason': 'vehicle already has active command', 'command_id': cid}
        if action in ('takeoff', 'goto_location') and not self.vehicles[uid]['armed']:
            return {'status': 'rejected', 'reason': 'mock vehicle is not armed', 'command_id': cid}
        self.commands[cid] = {**payload, 'status': 'queued', 'accepted_ns': self.now, 'stable_ns': None}
        self.log('command_accepted', **payload)
        return {'status': 'accepted', 'command_id': cid, 'sim_ns': self.now}

    def cancel(self, payload):
        c = self.commands.get(payload['command_id'])
        if c is None or c['vehicle'] != payload['vehicle'] or c['status'] in ('succeeded', 'failed', 'canceled'):
            return {'status': 'rejected', 'reason': 'unknown or terminal command'}
        c.update(status='cancel_requested', stable_ns=None)
        self.log('cancel_accepted', **payload)
        return {'status': 'accepted', 'command_id': payload['command_id'], 'sim_ns': self.now}

    def advance_flight(self, payload):
        target = payload['to_sim_ns']
        if target < self.now:
            raise ValueError('mock clock cannot regress')
        dt = (target-self.now)/NS
        self.now = target
        updates, events, receipts = [], [], []
        before = {uid: v['position'].copy() for uid, v in self.vehicles.items()}
        for cid, c in self.commands.items():
            if c['status'] in ('succeeded', 'failed', 'canceled'):
                continue
            def emit(status, reason=None):
                c['status'] = status
                row = {'command_id': cid, 'vehicle': c['vehicle'], 'action': c['action'], 'status': status, 'sim_ns': self.now}
                if reason: row['reason'] = reason
                updates.append(row)
                self.log('flight_update', **row)
            if c['status'] == 'queued':
                emit('running')
            v, profile = self.vehicles[c['vehicle']], self.scene['vehicles'][c['vehicle']]
            canceling = c['status'] == 'cancel_requested'
            action = 'hold' if canceling else c['action']
            destination = None
            if action == 'arm': v['armed'] = True
            elif action == 'disarm':
                if v['landed'] != 'ON_GROUND':
                    emit('failed', 'mock disarm rejected while airborne');continue
                v['armed'] = False
            elif action == 'takeoff':
                destination = np.array([v['position'][0], v['position'][1], v['home_z']+c['params']['altitude_m']])
                v['mode'], v['landed'] = 'TAKEOFF', 'TAKING_OFF'
            elif action == 'goto_location':
                destination = np.array(c['params']['position_enu'],float)
                v['mode'], v['landed'] = 'MISSION', 'IN_AIR'
            elif action == 'land':
                destination = np.array([v['position'][0], v['position'][1], v['home_z']])
                v['mode'], v['landed'] = 'LAND', 'LANDING'
            elif action == 'hold': v['mode'] = 'HOLD'
            desired = np.zeros(3)
            if destination is not None:
                delta = destination-v['position']; distance = float(np.linalg.norm(delta))
                accel = float(self.config.get('acceleration_m_s2', 3.0))
                speed = min(float(profile.get('speed_m_s',5.0)), math.sqrt(max(0.,2*accel*distance)))
                if distance > .03: desired = delta/distance*speed
            change = desired-v['velocity']; norm = float(np.linalg.norm(change))
            limit = float(self.config.get('acceleration_m_s2',3.0))*dt
            v['velocity'] += change * min(1.,limit/norm) if norm else change
            v['position'] += v['velocity']*dt
            if destination is not None and np.linalg.norm(destination-v['position']) < .08 and np.linalg.norm(v['velocity']) < 1.0:
                v['position'] = destination;v['velocity'][:] = 0
            collision = next((o for o in self.scene.get('public_map',{}).get('obstacles',[])
                if all(o['lower'][i] < v['position'][i] < o['upper'][i] for i in range(3))), None)
            if collision and v['landed'] != 'ON_GROUND':
                v['velocity'][:] = 0;emit('failed', 'mock geometry collision: '+collision['id']);continue
            yaw_reached = True
            if action == 'goto_location':
                # MAVSDK yaw is compass heading: north=0, east=90. SDF yaw is ENU.
                yaw_goal = math.pi/2-math.radians(c['params']['yaw_deg'])
                yaw_error = (yaw_goal-v['yaw']+math.pi)%(2*math.pi)-math.pi
                yaw_step = math.radians(float(self.config.get('yaw_rate_deg_s',45)))*dt
                v['yaw'] += max(-yaw_step,min(yaw_step,yaw_error))
                yaw_reached = abs(yaw_error) <= yaw_step + 1e-9
            reached = (destination is None or np.linalg.norm(destination-v['position']) <= .1) and yaw_reached
            reached = reached and float(np.linalg.norm(v['velocity'])) < .1
            if reached:
                if c['stable_ns'] is None:c['stable_ns']=self.now
                duration = 200_000_000 if action in ('arm','disarm') else NS
                if self.now-c['stable_ns'] >= duration:
                    if action == 'land':v.update(armed=False,landed='ON_GROUND')
                    elif action == 'takeoff':v['landed']='IN_AIR'
                    emit('canceled' if canceling else 'succeeded')
            else:c['stable_ns']=None
        for uid, v in self.vehicles.items():
            profile = self.scene['vehicles'][uid]
            moved = float(np.linalg.norm(v['position']-before[uid]))
            v['energy_used_j'] += moved*profile.get('energy_per_m_j',12)
            if v['armed'] and v['landed'] != 'ON_GROUND':
                v['energy_used_j'] += dt*profile.get('hover_power_w',250)
        self.advance_payload(dt,events,receipts)
        return self.flight_result(updates,events,receipts)

    def payload_rpc(self, request):
        if self.payload is None or request['vehicle'] != self.payload['carrier'] or request['package_id'] != self.payload['package_id']:
            return {'status':'unavailable','reason':'no mock payload for this vehicle'}
        if request['action'] == 'release' and not self.payload['released']:
            if self.config.get('joint_release_blocked', False):
                # Accept the actuator request while the simulated joint stays
                # attached. No free parcel/contact evolution can start here.
                self.payload['observed_sim_ns'] = self.now
                self.log('payload_release_blocked', request=request, state=dict(self.payload),
                         command_status='accepted', reason='explicit_mock_joint_release_blocked')
                return {'status': 'accepted', 'package_id': request['package_id'],
                        'accepted_sim_ns': self.now, 'released': False, 'joint_status': 'attached',
                        'source': 'mock_detachable_joint', 'source_time_ns': self.payload['source_time_ns'],
                        'observed_sim_ns': self.now, 'execution_backend': 'mock'}
            v=self.vehicles[request['vehicle']]
            self.payload.update(status='detached',released=True,source_time_ns=self.now,observed_sim_ns=self.now,
                release_interval_ns=[self.now,self.now],released_ns=self.now,
                position=(v['position']+self.scene['payload']['attachment_offset_enu_m']).tolist(),
                velocity=(v['velocity']+np.array(self.config.get('wind_enu_m_s',[0,0,0]))).tolist())
            self.log('payload_release',state=dict(self.payload))
            return {'status':'accepted','package_id':request['package_id'],'accepted_sim_ns':self.now,'released':True,
                    'source':'mock_detachable_joint','source_time_ns':self.now,'execution_backend':'mock'}
        # Position is simulator private; it is never an actuator-status field.
        self.payload['observed_sim_ns'] = self.now
        return {k:v for k,v in self.payload.items() if k not in ('position','velocity','contact_since','receipt_sent','contact_impulse_applied')}

    def advance_payload(self, dt, events, receipts):
        p=self.payload
        if not p or not p['released']:return
        cfg=self.scene['payload'];pos=np.array(p['position']);vel=np.array(p['velocity'])
        if not p.get('landed'):
            vel[2]-=9.81*dt;pos+=vel*dt
            surface=0.
            for o in self.scene.get('public_map',{}).get('obstacles',[]):
                if o['lower'][0]<=pos[0]<=o['upper'][0] and o['lower'][1]<=pos[1]<=o['upper'][1]:surface=max(surface,o['upper'][2])
            inside=all(abs(pos[i]-cfg['receiver_pose'][i])<=cfg['receiver_size'][i]/2 for i in (0,1))
            top=cfg['receiver_pose'][2]+cfg['receiver_size'][2]/2
            if inside:surface=max(surface,top)
            half=cfg['package_size_m'][2]/2
            if pos[2]<=surface+half:
                pos[2]=surface+half;vel[:]=0;p['landed']=True
                if inside and abs(surface-top)<1e-6:p['contact_since']=self.now
                self.log('payload_landed',position_enu_m=pos.tolist(),inside_receiver=inside)
            p['position'],p['velocity']=pos.tolist(),vel.tolist()
            if p.get('contact_impulse_applied'):
                self.log('payload_motion', position_enu_m=pos.tolist(), velocity_enu_m_s=vel.tolist(),
                         landed=bool(p.get('landed')), source='explicit_mock_contact_impulse')
        impulse = self.config.get('payload_contact_impulse')
        if (impulse and p.get('contact_since') is not None and not p.get('contact_impulse_applied')
                and self.now-p['contact_since'] >= impulse['after_contact_ns']):
            # A declared one-shot mechanical disturbance, not a suppressed
            # receipt: the parcel leaves the surface and resumes gravity-driven
            # flight. Later physical contact is evaluated by the same rule.
            velocity = np.asarray(p['velocity']) + np.asarray(impulse['velocity_delta_enu_m_s'], dtype=float)
            self.log('payload_contact_interrupted', contact_start_ns=p['contact_since'],
                     contact_end_ns=self.now, contact_duration_ns=self.now-p['contact_since'],
                     position_enu_m=p['position'], velocity_enu_m_s=velocity.tolist(),
                     source='explicit_mock_contact_impulse', parameters=impulse)
            p.update(landed=False, contact_since=None, velocity=velocity.tolist(), contact_impulse_applied=True)
        if p.get('contact_since') is not None and not p.get('receipt_sent') and self.now-p['contact_since']>=cfg['contact_duration_ns']:
            receipt={'source':'mock_contact_model','execution_backend':'mock','receiver_id':cfg['receiver_id'],
                'package_id':cfg['package_id'],'source_topic':cfg['contact_topic'],'clock_epoch':self.epoch,
                'contact_start_ns':p['contact_since'],'contact_end_ns':self.now,
                'contact_duration_ns':self.now-p['contact_since'],'source_clock':'mock_episode',
                'receipt_id':cfg['package_id']+'-'+self.epoch+'-contact'}
            receipts.append(receipt);p['receipt_sent']=True;self.log('payload_contact',receipt=receipt)

    def reset_radio(self,payload):
        self.nodes={n['id']:list(n['position_enu']) for n in payload['nodes']}
        self.radio_config=payload
        return {'reached_sim_ns':0,'time_quantum_ns':1,'configuration':payload}

    def send_packet(self,payload):
        p=dict(payload['params']);p['sent_ns']=self.radio_now
        latency=int(self.config.get('radio_delay_ns',40_000_000))+int(p['size']*8/float(self.config.get('radio_bitrate_bps',6_000_000))*NS)
        positions=self.nodes
        distance=math.dist(positions[p['src']],positions[p['dst']])
        loss=self.radio_config.get('propagation',{}).get('exponent',2.4)*10*math.log10(max(distance,1))+40
        # Axis-aligned volume intersection, used only by the declared RF model.
        a=np.array(positions[p['src']]);b=np.array(positions[p['dst']]);direction=b-a
        for volume in self.radio_config.get('scene_volumes',[]):
            lo,hi=0.,1.
            for i in range(3):
                if abs(direction[i])<1e-12:
                    if not volume['min_enu'][i]<=a[i]<=volume['max_enu'][i]:lo,hi=1,0;break
                else:
                    q=sorted(((volume['min_enu'][i]-a[i])/direction[i],(volume['max_enu'][i]-a[i])/direction[i]));lo=max(lo,q[0]);hi=min(hi,q[1])
            if hi>=lo:loss+=volume['loss_db']
        rssi=self.radio_config.get('channel',{}).get('tx_power_dbm',20)-loss
        lost=(distance>float(self.config.get('radio_range_m',2000)) or rssi<float(self.config.get('receiver_sensitivity_dbm',-92))
              or self.rng.random()<float(self.config.get('packet_loss_rate',0)))
        lifetime=int(p.get('lifetime_ns',5*NS))
        p.update(due_ns=self.radio_now+(lifetime if lost or latency>lifetime else latency),
                 lost=lost or latency>lifetime,rssi=rssi)
        self.packets.append(p)
        return {'status':'accepted','packet_id':p['packet_id'],'sim_ns':self.radio_now}

    def advance_radio(self,payload):
        target=payload['to_sim_ns'];deliveries=[];drops=[]
        for m in payload.get('mobility',[]):self.nodes[m['node']]=m['position']
        for p in list(self.packets):
            if p['due_ns']>target:continue
            self.packets.remove(p)
            if p['lost']:
                drops.append({k:p[k] for k in ('packet_id','src','dst')}|{'sim_ns':p['due_ns'],'available_sim_ns':target,'reason':'delivery_timeout'})
            else:
                deliveries.append({k:p[k] for k in ('packet_id','src','dst','sent_ns','size','payload_ref')}|{
                    'received_ns':p['due_ns'],'available_sim_ns':target,'rssi_dbm':p['rssi'],'snr_db':p['rssi']+100})
        self.radio_now=target
        return {'reached_sim_ns':target,'deliveries':deliveries,'drops':drops,'link_stats':[]}

    def capture(self,request):
        uid=request['vehicle']
        if request.get('action')=='read':
            frame=self.frames.get((uid,request['frame_id']))
            if not frame:return {'status':'unavailable','reason':'mock frame expired'}
            data=frame[1][request['kind']];offset=request.get('offset',0);size=request.get('max_bytes',262144)
            return {'status':'succeeded','frame_id':request['frame_id'],'kind':request['kind'],'offset':offset,
                    'total_bytes':len(data),'data_base64':base64.b64encode(data[offset:offset+size]).decode()}
        if self.now<=request.get('after_sim_ns',-1):return {'status':'pending','reason':'next mock frame not acquired'}
        metadata,blobs=self.render(uid)
        key=(uid,metadata['frame_id']);self.frames[key]=(metadata,blobs)
        owned=[k for k in self.frames if k[0]==uid]
        for old in owned[:-4]:del self.frames[old]
        return {'status':'succeeded','observation':metadata,'blobs':{k:{'bytes':len(v),
            'sha256':hashlib.sha256(v).hexdigest(),'encoding':'png' if k=='rgb' else 'zlib-float32-le-metres'} for k,v in blobs.items()}}

    def render(self,uid):
        c=self.scene['simulator']['rgbd_camera'];w,h=c['width'],c['height']
        fx=w/(2*math.tan(c['horizontal_fov_rad']/2));k=np.array([[fx,0,w/2],[0,fx,h/2],[0,0,1.]])
        # Same SDF mount convention as the platform; independent explicit math.
        x,y,z,r,p,yaw=c['pose_flu'];cr,sr,cp,sp,cy,sy=math.cos(r),math.sin(r),math.cos(p),math.sin(p),math.cos(yaw),math.sin(yaw)
        rot=np.array([[cy*cp,cy*sp*sr-sy*cr,cy*sp*cr+sy*sr],[sy*cp,sy*sp*sr+cy*cr,sy*sp*cr-cy*sr],[-sp,cp*sr,cp*cr]])
        t=np.eye(4);t[:3,:3]=np.diag([1,-1,-1])@rot@np.array([[0,0,1],[-1,0,0],[0,-1,0]]);t[:3,3]=[x,-y,-z]
        estimate=self.estimate(uid);body=quaternion_matrix(estimate['attitude_body_frd_to_world_enu_xyzw'])
        origin=np.array(estimate['position_enu_m'])+body@t[:3,3];rotation=body@t[:3,:3]
        yy,xx=np.mgrid[:h,:w];rays=np.stack([(xx-w/2)/fx,(yy-h/2)/fx,np.ones((h,w))],axis=-1)@rotation.T
        depth=np.full((h,w),np.inf,dtype=np.float64);rgb=np.zeros((h,w,3),dtype=np.uint8);rgb[:]=[116,157,175]
        # Ground plane and full public AABBs, including walls that occlude a
        # lower camera; depth stores optical Z (ray parameter), not ray length.
        with np.errstate(divide='ignore', invalid='ignore'):
            ground = -origin[2]/rays[:,:,2]
        valid=(ground>=c['near_m'])&(ground<=c['far_m'])
        depth[valid]=ground[valid];rgb[valid]=[95,100,105]
        for index,o in enumerate(self.scene.get('public_map',{}).get('obstacles',[])):
            entry=np.full((h,w),-np.inf);exit=np.full((h,w),np.inf)
            for axis in range(3):
                direction=rays[:,:,axis];parallel=np.abs(direction)<1e-12
                with np.errstate(divide='ignore',invalid='ignore'):
                    one=(o['lower'][axis]-origin[axis])/direction
                    two=(o['upper'][axis]-origin[axis])/direction
                lower=np.minimum(one,two);upper=np.maximum(one,two)
                inside=o['lower'][axis]<=origin[axis]<=o['upper'][axis]
                lower=np.where(parallel,-np.inf if inside else np.inf,lower)
                upper=np.where(parallel,np.inf if inside else -np.inf,upper)
                entry=np.maximum(entry,lower);exit=np.minimum(exit,upper)
            dist=np.where(entry>=c['near_m'],entry,exit)
            valid=(exit>=entry)&(dist>=c['near_m'])&(dist<=c['far_m'])&(dist<depth)
            depth[valid]=dist[valid];rgb[valid]=[108+index*13%60,113+index*13%60,118+index*13%60]
        image=Image.fromarray(rgb)
        # Private camera-facing billboards are a declared mock scene primitive.
        for person in self.config.get('people',[]):
            point=np.array(person['position_enu_m']);camera=rotation.T@(point-origin)
            if camera[2]<=c['near_m']:continue
            u,v=fx*camera[:2]/camera[2]+[w/2,h/2];height=float(person.get('height_m',1.75));width=float(person.get('width_m',.65))
            pw,ph=max(1,round(fx*width/camera[2])),max(1,round(fx*height/camera[2]));left,top=round(u-pw/2),round(v-ph/2)
            if left+pw<=0 or top+ph<=0 or left>=w or top>=h:continue
            sprite=person.get('sprite_path') or self.config.get('person_sprite_path')
            if sprite:
                with Image.open(sprite) as src:patch=src.convert('RGBA').resize((pw,ph))
            else:
                patch=Image.new('RGBA',(pw,ph));draw=ImageDraw.Draw(patch);draw.ellipse((pw*.25,0,pw*.75,ph*.24),fill=(199,155,122,255));draw.rectangle((pw*.2,ph*.22,pw*.8,ph*.68),fill=(230,85,35,255));draw.line((pw*.35,ph*.65,pw*.3,ph),fill=(25,30,45,255),width=max(1,pw//4));draw.line((pw*.65,ph*.65,pw*.7,ph),fill=(25,30,45,255),width=max(1,pw//4))
            xa,ya,xb,yb=max(0,left),max(0,top),min(w,left+pw),min(h,top+ph)
            alpha=np.array(patch)[ya-top:yb-top,xa-left:xb-left,3]>0
            visible=alpha&(camera[2]<=depth[ya:yb,xa:xb]+.05)
            arr=np.array(image);piece=np.array(patch)[ya-top:yb-top,xa-left:xb-left,:3];arr[ya:yb,xa:xb][visible]=piece[visible];image=Image.fromarray(arr)
            depth[ya:yb,xa:xb][visible]=camera[2]
        # The same moving box surface supplies RGB, occlusion and optical-Z.
        # Pose is private simulator state; only rendered sensor arrays leave it.
        rgb = np.asarray(image).copy()
        self.render_parcel(rgb, depth, origin, rays, c)
        image = Image.fromarray(rgb)
        stream=io.BytesIO();image.save(stream,format='PNG');png=stream.getvalue()
        depth[~np.isfinite(depth)]=np.nan
        metadata={'run_id':self.run_id,'uav_id':uid,'sensor_id':c['sensor_id'],
            'frame_id':f'{self.run_id}:{self.epoch}:{uid}:rgbd:{self.now}',
            'source':'mock_scene_renderer','execution_backend':'mock','source_clock':'mock_episode','source_time':self.now,
            'clock_epoch':self.epoch,'observed_at':self.now,'depth_observed_at':self.now,'available_sim_ns':self.now,
            'coordinate_frame':'camera_optical','vehicle_estimate':estimate,'localization_status':'available',
            'calibration':{'K':k.flatten().tolist(),'width':w,'height':h,'distortion':[],'distortion_model':'plumb_bob',
                'orientation':c.get('orientation','configured'),'horizontal_fov_deg':math.degrees(c['horizontal_fov_rad']),
                'vertical_fov_deg':math.degrees(2*math.atan(h/(2*fx))),'registered_rgb_depth':True,
                'depth_convention':'optical_z','depth_min_m':c['near_m'],'depth_max_m':c['far_m'],
                'T_body_frd_from_camera_optical':t.tolist(),'extrinsic_source':'explicit mock camera mount',
                'version':self.epoch+':camera','render_model':'public boxes, private camera-facing person sprites, axis-aligned moving parcel with optional public top marker'},
            'rgb_sha256':hashlib.sha256(png).hexdigest()}
        return metadata,{'rgb':png,'depth':zlib.compress(depth.astype('<f4').tobytes(),3)}

    def render_parcel(self, rgb, depth, origin, rays, camera):
        """Ray-cast a world-ENU-aligned box; this mock has no parcel rotation."""
        if not self.payload:
            return
        config = self.scene['payload']
        size = np.asarray(config['package_size_m'], dtype=float)
        if self.payload['released']:
            center = np.asarray(self.payload['position'], dtype=float)
        else:
            center = self.vehicles[self.payload['carrier']]['position'] + config['attachment_offset_enu_m']
        lower, upper = center-size/2, center+size/2
        entry, exit = np.full(depth.shape, -np.inf), np.full(depth.shape, np.inf)
        for axis in range(3):
            direction = rays[:, :, axis]
            parallel = np.abs(direction) < 1e-12
            with np.errstate(divide='ignore', invalid='ignore'):
                one = (lower[axis]-origin[axis])/direction
                two = (upper[axis]-origin[axis])/direction
            lo, hi = np.minimum(one, two), np.maximum(one, two)
            inside = lower[axis] <= origin[axis] <= upper[axis]
            entry = np.maximum(entry, np.where(parallel, -np.inf if inside else np.inf, lo))
            exit = np.minimum(exit, np.where(parallel, np.inf if inside else -np.inf, hi))
        distance = np.where(entry >= camera['near_m'], entry, exit)
        visible = ((exit >= entry) & (distance >= camera['near_m']) &
                   (distance <= camera['far_m']) & (distance < depth))
        if not visible.any():
            return
        depth[visible] = distance[visible]
        rgb[visible] = [224, 115, 25]
        marker = config.get('visual_marker')
        if marker is None:
            return  # An unmarked box never receives an implicit identity tag.
        import cv2
        marker_size = float(marker['size_m'])
        if not 0 < marker_size < min(size[:2]):
            raise ValueError('marker must fit on the parcel top with a white margin')
        dictionary_name = marker['dictionary']
        if not dictionary_name.startswith('DICT_') or not hasattr(cv2.aruco, dictionary_name):
            raise ValueError('unsupported public marker dictionary')
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
        texture = cv2.aruco.generateImageMarker(dictionary, int(marker['id']), 256,
                                               borderBits=int(marker.get('border_bits', 1)))
        rows, columns = np.nonzero(visible)
        points = origin + distance[rows, columns, None]*rays[rows, columns]
        top = (np.abs(points[:, 2]-upper[2]) <= 1e-6) & (rays[rows, columns, 2] < 0)
        # Top is white outside the tag; .24 m is the black border's outer size.
        rgb[rows[top], columns[top]] = 255
        u = (points[:, 0]-center[0])/marker_size + .5
        v = .5-(points[:, 1]-center[1])/marker_size
        tagged = top & (u >= 0) & (u < 1) & (v >= 0) & (v < 1)
        gray = texture[(v[tagged]*256).astype(int), (u[tagged]*256).astype(int)]
        rgb[rows[tagged], columns[tagged]] = gray[:, None]


class MockRpcClient:
    """In-process JSON request/reply transport, explicitly selected before start."""
    def __init__(self, world: MockWorld, backend: str):
        self.world,self.backend=world,backend
        self.protocol='aeroagentsim.px4/v1' if backend=='flight' else 'aeroagentsim.ns3/v1'
        self.budget=ResourceBudget(frame_bytes=8*1024*1024);self.tainted=False;self.sequence=0

    def request(self,operation,payload):
        if self.tainted:raise RuntimeError('mock backend tainted')
        self.sequence+=1
        request=json.loads(json.dumps({'protocol':self.protocol,'major':1,'minor':0,'id':self.sequence,'op':operation,'payload':payload},allow_nan=False))
        if operation=='hello':
            caps={'timing':'lockstep','exact_stop':True,'hold':True,'max_frame_bytes':8*1024*1024,
                'actions':['arm','takeoff','goto_location','hold','land','disarm'] if self.backend=='flight' else ['send']}
            caps.update({'physics_step_ns':4_000_000,'vehicles':sorted({v.get('model','x500') for v in self.world.scene['vehicles'].values()}),'sensors':['gazebo_pose','mavsdk_telemetry','gazebo_contacts']} if self.backend=='flight' else {'time_quantum_ns':1})
            result={'protocol':self.protocol,'major':1,'minor':0,'capabilities':caps}
        elif operation=='close':result={'closed':True}
        else:
            function={'flight':{'reset':self.world.reset_flight,'advance':self.world.advance_flight,'command':self.world.command,'cancel':self.world.cancel,'capture':self.world.capture,'payload':self.world.payload_rpc},
                'radio':{'reset':self.world.reset_radio,'advance':self.world.advance_radio,'command':self.world.send_packet}}[self.backend][operation]
            result=function(request['payload'])
        response=json.loads(json.dumps({'protocol':self.protocol,'major':1,'minor':0,'id':self.sequence,'result':result},allow_nan=False))
        # Full image chunks are not repeated in this diagnostic ledger.
        if operation!='advance':
            with (self.world.root/'mock-rpc.jsonl').open('a') as out:
                out.write(json.dumps({'execution_backend':'mock','backend':self.backend,'request':request,
                    'response':response if operation!='capture' else {'status':result.get('status'),'frame_id':result.get('frame_id')},'sim_ns':self.world.now})+'\n')
        return response['result']

    def abort(self):self.tainted=True
    def disconnect(self):pass
