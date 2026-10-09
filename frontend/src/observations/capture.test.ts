import {it,expect} from 'vitest';
import {recordedCameraPose} from './capture';
it('reads the traffic bridge flat pose snapshot without inventing a nested actor reference',()=>{
 const pose={id:'uav.bravo',generation:{$integer:'9007199254740993'},position:[12,34,70],field:'he.aircraft.position_enu_m',version:{journal_index:52,item_ordinal:0},acquired:{clock_id:'canonical'}};
 expect(recordedCameraPose(pose)).toEqual({key:{id:'uav.bravo',generation:'9007199254740993'},position:[12,34,70],field:'he.aircraft.position_enu_m'});
 expect(()=>recordedCameraPose({...pose,position:undefined})).toThrow('finite 3-vector');
});
