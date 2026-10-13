import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect,it } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import type { PlanPreview,PlanVersion } from './api';
import { RegionProposal,proposalGeography } from './RegionProposal';

afterEach(()=>{cleanup();localStorage.clear();});
const plan:PlanVersion={version:1,created_at:'',note:'',base:{},title:'Region',summary:'',assumptions:[],open_questions:[],compile:{ok:true,errors:[],warnings:[]},validation:{state:'valid'},
  operations:[{id:'op-region',op:'set_region',params:{extract:'wujiaochang'}}],scene:{places:[{id:'pad',role:'charging_pad',label:'Central pad',position:[0,0,0]}]}};
const preview:PlanPreview={name:'Area',scenario:{},region_preview:{origin:{lon:121.5,lat:31.3,alt:0},bounds:[121.49,31.29,121.51,31.31],geometry:{
  buildings:{type:'FeatureCollection',features:[{geometry:{type:'Polygon',coordinates:[[[-10,-5,0],[10,-5,0],[10,5,0],[-10,-5,0]]]}}]},
  roads:[{id:'road-1',points:[[-20,0,0],[20,0,0]]}],truncated:false,
}}};
it('T4-R2-016: renders the proposal’s actual C10 buildings, road and place',()=>{
  render(<I18nProvider><RegionProposal plan={plan} preview={preview}/></I18nProvider>);
  expect(screen.getByRole('img')).toBeVisible();
  expect(screen.getByTestId('as-region-building')).toHaveAttribute('d','M-10,5 L10,5 L10,-5 L-10,5 Z');
  expect(screen.getByTestId('as-region-road')).toHaveAttribute('points','-20,0 20,0');
  expect(screen.getByRole('img').querySelector('circle title')).toHaveTextContent('Central pad');
  expect(screen.queryByTestId('as-geography-after-apply')).toBeNull();
});
it.each(['en-US','zh-CN'])('T4-R2-016: clearly states when proposal geography is unavailable in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  render(<I18nProvider><RegionProposal plan={plan}/></I18nProvider>);
  expect(screen.getByTestId('as-geography-after-apply')).toHaveTextContent(locale==='en-US'?'Geography appears after applying.':'应用后显示地理场景。');
  expect(screen.queryByRole('img')).toBeNull();
});
it('rejects malformed geography visibly and reports truncated geometry honestly',()=>{
  const {rerender}=render(<I18nProvider><RegionProposal plan={plan} preview={{...preview,region_preview:{...preview.region_preview!,bounds:[1,2,0,3]}}}/></I18nProvider>);
  expect(screen.getByRole('alert')).toHaveTextContent('The geographic preview could not be shown.');
  expect(screen.queryByRole('img')).toBeNull();
  rerender(<I18nProvider><RegionProposal plan={plan} preview={{...preview,region_preview:{...preview.region_preview!,geometry:{...preview.region_preview!.geometry,truncated:true}}}}/></I18nProvider>);
  expect(screen.getByText('This preview shows part of the source geometry.')).toBeVisible();
});
it('projects a compiled WGS84 scene with latitude scaling and keeps its ENU roads unchanged',()=>{
  const origin={lon:121.5,lat:31.3,alt:0},bounds=[121.49,31.29,121.51,31.31];
  const value:PlanPreview={name:'Compiled',scenario:{},scene:{origin,bounds,geojson:{type:'FeatureCollection',features:[
    {geometry:{type:'Polygon',coordinates:[[[121.5,31.3],[121.501,31.3],[121.501,31.301],[121.5,31.3]]]}},
    {geometry:{type:'LineString',coordinates:[[121.5,31.3],[121.501,31.3]]}},
  ]},roads:[{id:'road',points:[[-5,0,0],[5,0,0]],tags:{}}],ground:{width_m:1900,depth_m:2200},diagnostics:[],attribution:'OSM'}};
  const geometry=proposalGeography(value)!;
  expect(geometry.buildings).toHaveLength(1);
  const east=geometry.buildings[0][0][1][0];
  const rad=Math.PI/180,d=1-6.69437999014e-3*Math.sin(31.3*rad)**2;
  expect(east).toBeCloseTo(.001*6378137/Math.sqrt(d)*Math.cos(31.3*rad)*rad,5);
  expect(geometry.roads[0].points).toEqual([[-5,0],[5,0]]);
});
