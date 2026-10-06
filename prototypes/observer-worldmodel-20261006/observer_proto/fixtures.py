import numpy as np
from .contracts import FieldSpec, History, Observer, Observation, Association, Query, HISTORY


def tiny_history():
    times=np.linspace(-10,0,HISTORY)
    values=np.zeros((HISTORY,3,2))
    values[:,0,0]=np.linspace(-.1,0,HISTORY)
    values[:,1,0]=np.linspace(2.8,2.0,HISTORY)
    values[:,2,0]=4.; values[:,2,1]=1.
    shape=values.shape
    valid=np.ones(shape,dtype=bool)
    # Unknown past value remains missing, not zero-valued truth; present persists.
    valid[5:8,2]=False; values[5:8,2]=np.nan
    source=np.where(valid,1,0)
    time_grid=np.broadcast_to(times[:,None,None],shape).copy()
    observers=(Observer('camera_a','image','fixture:camera-calibration-v1'),
               Observer('lidar_a','point','fixture:lidar-calibration-v1'),
               Observer('logger_a','log','fixture:clock-calibration-v1'))
    observations=[]; associations=[]
    rng=np.random.default_rng(101)
    for i,o in enumerate(observers):
        observations.append(Observation(f'obs_{i}',o.observer_id,20,0.,0.,0.,
                          f'synthetic://{o.modality}/embedding-v1',rng.normal(size=(2,4)),np.zeros(6)))
        associations.append(Association(f'obs_{i}',('entity_a','entity_b','entity_c')[i],(i%2,),f'fixture:{o.modality}-local-region'))
    return History((FieldSpec('x'),FieldSpec('y')),('entity_a','entity_b','entity_c'),
                   np.array([0,0,1]),times,values,valid,source,np.full(shape,.1),
                   time_grid.copy(),time_grid.copy(),np.ones((HISTORY,3),bool),
                   observers,tuple(observations),tuple(associations)).validate()


def tiny_queries():
    return [Query('entity_a','entity_b',2.0),Query('entity_b','entity_c',2.5)]
