"""Build an offline, read-only scenario-graph projection; never a native evaluator."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent.parent
N=[]; E=[]
NS='demo.emergency_delivery.'
def node(id,kind,zh,en,group,summary_zh,summary_en=None,**props):
 d=dict(id=NS+id,kind=kind,label={'zh':zh,'en':en},group=group,description={'zh':summary_zh,'en':summary_en or summary_zh},revision='fixture-v1',provenance='authored_synthetic_fixture',**props); N.append(d); return d

def edge(a,b,rel,role,**props):
 E.append(dict(id=NS+'edge.'+str(len(E)+1).zfill(3),source=NS+a,target=NS+b,type=rel,role=role,direction='source_to_target',revision='fixture-v1',level=props.pop('level','definition'),**props))

def fact(id,zh,en,field,value,unit,entity,t,authority,**more):
 return node(id,'fact',zh,en,'facts','带主体、单位、有效时间、可用时间和来源的合成事实。','Synthetic fact with subject, units, valid time, availability and provenance.',field_id=NS+field,value=value,unit=unit,subject_ref={'run_id':'synthetic-emergency-001','epoch':1,'id':NS+entity,'generation':1,'ref_type':'entity'},valid_time_s=t,available_time_s=t+.1,valid_until_s=more.pop('valid_until_s',t+5),clock_domain='scenario_sim_seconds',authority=NS+authority,evidence_ref=NS+'evidence',**more)

kinds={
'entity_type':('实体类型','Entity type','world','entity_types'), 'entity':('实体','Entity','world','entities'),
'state_spec':('状态规格','State specification','facts','fields'),'fact':('事实记录','Fact record','facts','facts'),
'module':('模块','Module','execution','modules'),'module_execution':('模块执行记录','Module execution','execution',None),
'agent':('独立代理','Agent','decision','agents'),'strategy':('策略','Strategy','decision','strategies'),
'strategy_invocation':('策略调用','Strategy invocation','decision',None),'objective':('目标','Objective','decision','objectives'),
'task':('任务','Task','decision','tasks'),'capability':('能力','Capability','execution','capabilities'),
'resource':('资源','Resource','execution','resources'),'behavior':('行为定义','Behavior definition','execution','behaviors'),
'behavior_execution':('行为执行','Behavior execution','execution',None),'command':('指令记录','Command record','execution','commands'),
'rule':('规则表达式','Rule expression','rules','rules'),'predicate':('谓词定义','Predicate definition','rules','predicates'),
'predicate_result':('谓词结果','Predicate result','feedback',None),'event_def':('事件定义','Event definition','rules','events'),
'event_record':('事件记录','Event record','feedback',None),'constraint':('约束','Constraint','rules','constraints'),
'policy_source':('场景规则来源','Scenario policy source','rules','sources'),'decision':('决策','Decision','decision','decisions'),
'receipt':('指令回执','Command receipt','feedback',None),'evidence':('证据','Evidence','feedback','sources'),
'constraint_check':('执行约束检查','Constraint check','execution','admission_checks'),'check_result':('检查结果','Check result','execution',None),
'operator':('运算符表达式','Operator expression','rules',None),'state_ref':('状态引用','State reference','rules',None),
'parameter':('参数','Parameter','rules','parameter_definitions'),'constant':('常量','Constant','rules',None),
'target_ref':('谓词引用','Predicate reference','rules',None)
}
for t,zh,en in [('uav','无人机类型','UAV type'),('parcel','包裹类型','Parcel type'),('person','人员类型','Person type'),('camera','相机类型','Camera type'),('radio','无线电类型','Radio type'),('region','区域类型','Region type'),('endpoint','接收端类型','Endpoint type')]:
 node('type.'+t,'entity_type',zh,en,'world','示例实体类型；实例与类型分离。','Example entity class, separate from its instances.')
 node(t,'entity',{'uav':'无人机 A','parcel':'包裹 P','person':'虚拟儿童 C','camera':'相机 CAM-1','radio':'无线电 RF-1','region':'安全观察区域','endpoint':'演练接收端'}[t],{'uav':'UAV A','parcel':'Parcel P','person':'Synthetic child C','camera':'Camera CAM-1','radio':'Radio RF-1','region':'Observation region','endpoint':'Exercise recipient'}[t],'world',{'uav':'承载包裹并接受一个运动目标。代理是图中的独立节点。','parcel':'任务中始终保持无人机保管及物理挂载；不会遗弃。','person':'脚本中的虚构人员角色；没有真实儿童数据、身份识别或影像。','camera':'物理实体；拍摄占用的相机槽位另建为资源。','radio':'物理实体；无线带宽另建为资源。','region':'局部 ENU 坐标中的虚构区域，不是真实地点。','endpoint':'仅为演练授权的逻辑接收者；页面不发送任何数据。'}[t],{'uav':'Carries the parcel and accepts one motion target. The agent is independent.','parcel':'Custody and physical attachment remain with the UAV. Never abandoned.','person':'Fictional scripted role. No real child data, identification or imagery.','camera':'Physical device; camera-use capacity is a separate resource.','radio':'Physical device; link capacity is a separate resource.','region':'Fictional region in a local ENU frame, not a real place.','endpoint':'Scenario-authorized logical recipient only. This page sends no data.'}[t],entity_type=NS+'type.'+t)
 edge(t,'type.'+t,'instance_of','class')

fields=[('confidence','跌倒报告置信度','Fall-report confidence','number','1','person','perception'),('separation','与人员的最小距离','Separation from person','number','m','uav','gazebo'),('energy','剩余能量估计','Estimated energy','number','Wh','uav','energy_module'),('pose','实际位置','Actual pose','vector3','m','uav','gazebo'),('target','已接受运动目标','Accepted motion target','entity_ref','1','uav','px4'),('task_state','配送任务状态','Delivery task state','string','1','parcel','business'),('custody','责任保管人','Responsible custodian','entity_ref','1','parcel','business'),('attachment','物理挂载','Physical attachment','entity_ref','1','parcel','gazebo'),('upload','上传状态','Upload status','string','1','radio','ns3'),('media','最小化拍摄产物','Minimized capture artifact','string','1','camera','perception')]
for f,zh,en,typ,unit,owner,auth in fields:
 node('state.'+f,'state_spec',zh,en,'facts','字段规格，不是运行中的值。单一计算权属：'+auth,'A field specification, not a runtime value. Single computing authority: '+auth,value_type=typ,unit=unit,frame='local_ENU' if f=='pose' else 'not_applicable',authority=NS+auth)
 edge(owner,'state.'+f,'owns_state','subject');edge(auth,'state.'+f,'updates_state','computing_authority')
fact('fact.incident','疑似跌倒 · 0.91','Suspected fall · 0.91','state.confidence',.91,'1','person',12,'perception',observation_only=True,ground_truth='not_established')
fact('fact.separation','安全距离 · 12 m','Separation · 12 m','state.separation',12,'m','uav',20,'gazebo')
fact('fact.energy','剩余能量 · 60 Wh','Energy · 60 Wh','state.energy',60,'Wh','uav',12,'energy_module')
fact('fact.pose','到达观察点','At observation point','state.pose',[35,12,15],'m','uav',20,'gazebo',frame='local_ENU')
fact('fact.target','已接受：观察点','Accepted: observation point','state.target',NS+'region','1','uav',13,'px4',valid_until_s=38,exclusive_channel='motion')
fact('fact.paused','配送任务已暂停','Delivery paused','state.task_state','paused','1','parcel',13,'business',valid_until_s=38)
fact('fact.custody','包裹仍由 A 保管','Parcel stays in A custody','state.custody',NS+'uav','1','parcel',13,'business',valid_until_s=120)
fact('fact.attachment','包裹保持挂载','Parcel remains attached','state.attachment',NS+'uav','1','parcel',13,'gazebo',valid_until_s=120)
fact('fact.capture','已生成最小化画面','Minimized frame created','state.media','synthetic-placeholder-only','1','camera',22,'perception',payload_bytes=0,privacy='No imagery stored; synthetic metadata only')
fact('fact.upload','上传成功 · 16 s','Upload success · 16 s','state.upload','acknowledged','1','radio',38,'ns3',valid_until_s=39,latency_s=16,network_action='none; fixture trace')
fact('fact.after_energy','结算后 · 56.90 Wh','Settled · 56.90 Wh','state.energy',56.9,'Wh','uav',38,'energy_module',valid_until_s=39,ledger_id=NS+'ledger.001')
for d in list(N):
 if d['kind']=='fact': edge(d['id'].removeprefix(NS),d['field_id'].removeprefix(NS),'instantiates_state','runtime_value',level='runtime')

for id,zh,en,desc in [('px4','PX4 飞控模块','PX4 flight module','Owns accepted target and control receipts, not actual pose.'),('gazebo','Gazebo 物理模块','Gazebo physics module','Owns actual pose and physical attachment.'),('ns3','ns-3 网络模块','ns-3 network module','Owns simulated latency and transport results.'),('energy_module','能量模块','Energy module','Only energy ledger authority; charges each interval once.'),('business','业务/任务模块','Business/task module','Owns task state and versioned custody records.'),('perception','观察/相机模块','Observation/camera module','Owns reported confidence and synthetic capture artifacts; no ground-truth access.')]:
 node(id,'module',zh,en,'execution',{'px4':'仅拥有接收目标与控制回执，不冒充实际位置。','gazebo':'拥有实际位置与物理挂载事实。','ns3':'拥有模拟网络时延与传输结果。','energy_module':'唯一能量账本；同一段消耗只计一次。','business':'拥有任务生命周期和保管记录。','perception':'输出观测和合成拍摄产物，不将报告当作真实跌倒。'}[id],desc,connection='not_connected',implementation_claim='conceptual binding only')
node('agent','agent','任务代理','Mission agent','decision','实体外的独立决策参与者。权限仅限本次合成演练。','Independent decision participant outside the UAV. Authority is limited to this authored scenario.',authority={'actor':NS+'agent','target':NS+'uav','operations':['motion_target','camera_capture','upload_to_exercise_recipient'],'valid_s':[0,120],'source':NS+'policy','arbitration':'exclusive_motion_channel'})
edge('agent','uav','controls','scenario_control_scope',authority_ref=NS+'policy',level='runtime')
node('strategy','strategy','安全优先的任务重规划','Safety-first replanning','decision','读可见事实、任务目标和适用约束。输出决策，不直接改写物理事实。','Reads visible facts, goals and applicable constraints; emits a decision, never physical facts.')
node('invocation','strategy_invocation','重规划调用 @12.1 s','Replan call @12.1 s','decision','只使用到 12.1 s 可用的记录；更晚的到达或上传记录不能回填本次决策。','Uses only records available by 12.1 s. Later arrival/upload facts cannot leak into this decision.',query_s=12.1,available_cutoff_s=12.1)
edge('agent','strategy','calls_strategy','selected_revision');edge('invocation','strategy','instance_of','selected_strategy',level='runtime')
node('goal','objective','完成配送 + 安全记录异常','Delivery + safe incident capture','decision','原始配送目标保留。演练要求在满足安全与授权条件时记录异常。','Keep the delivery objective. Capture an incident only under the scenario safety and authorization conditions.')
node('task','task','配送任务 · 暂停/恢复','Delivery task · pause/resume','decision','t=13 暂停配送，物理包裹与责任保管不变；上传结果触发再次规划。','Pause at t=13; parcel custody and attachment remain. Upload outcome triggers replanning.',initial_status='active',status_at_incident='paused',attempt=1)
node('decision','decision','暂停配送，转向观察点','Pause delivery; observe safely','decision','替换唯一的已接受运动目标；不同时执行配送航点和异常观察航点。','Replace the one accepted motion target. Delivery and observation targets do not run concurrently.',chosen_at_s=12.1)
node('command','command','异常记录组合指令','Incident-capture command','execution','序列：靠近安全点 → 拍摄 → 上传；条件分支：恢复 / 返航 / 等待。保持与上传可并行但资源不同。','Sequence: safe approach → capture → upload. Conditional resume / return / await. Hold and upload may overlap on distinct resources.',target=NS+'uav',record_kind='authored_command_record',issued_at_s=12.2)
edge('goal','strategy','input_to','objective');edge('fact.incident','strategy','input_to','observation');edge('fact.energy','strategy','input_to','observation');edge('task','strategy','input_to','request',extension=True)
edge('invocation','fact.incident','reads_fact','observation',level='runtime',available_cutoff_s=12.1);edge('invocation','fact.energy','reads_fact','energy_budget',level='runtime',available_cutoff_s=12.1)
edge('invocation','decision','produces_decision','chosen_plan',level='runtime');edge('decision','command','generates_command','authorized_execution',level='runtime')
node('policy','policy_source','演练规则 v1','Exercise policy v1','rules','作者编写的演练规则，不是法律。仅授权合成数据向演练接收端记录异常；不做人脸或身份识别。','Authored exercise policy, not law. Only synthetic incident evidence for the exercise recipient; no facial recognition or identification.',legal_status='not_a_regulatory_source',recipient=NS+'endpoint',purpose='exercise incident review',minimization='synthetic metadata; no actual images; no network upload')
for id,zh,en,desc in [('constraint.incident','异常记录要求','Incident-capture requirement','When suspected incident has admissible evidence, safely record it under exercise policy.'),('constraint.safety','安全与单一运动目标','Safety + one motion target','Maintain synthetic separation >8 m and positive reserved energy; start/continue checks required.'),('constraint.privacy','定向上传与数据最小化','Recipient + minimization','Only the exercise recipient and purpose; synthetic metadata only; no real personal data.')]:
 node(id,'constraint',zh,en,'rules',{'constraint.incident':'演练规则要求记录疑似异常，不代表真实法律义务。','constraint.safety':'演练阈值：距离 >8 m，保留返航能量，并检查单一运动目标。','constraint.privacy':'接收方、目的与最小化范围必须明确；未知授权不放行。'}[id],desc,applicability='applicable',enforcement='authored start/continue check trace',source=NS+'policy')
 edge(id,'policy','based_on_policy','authored_exercise_source',extension=True)
 edge(id,'strategy','input_to','applicable_constraint');edge('strategy',id,'output_must_satisfy','hard_requirement')
# Repeated endpoints with different typed edges are intentional.
edge('strategy','constraint.safety','checked_against','decision_review',extension=True)

rules=[('rule.incident','高置信度疑似跌倒','High-confidence suspected fall',{'op':'gt','args':[{'state':NS+'state.confidence'},{'param':'incident_threshold'}]}),('rule.safety','观察距离满足要求','Observation distance requirement',{'op':'gt','args':[{'state':NS+'state.separation'},{'const':8,'unit':'m'}]}),('rule.composite','允许执行安全记录','Safe capture eligibility',{'op':'and','args':[{'predicate':NS+'predicate.incident'},{'predicate':NS+'predicate.safety'}]})]
for rid,zh,en,ast in rules:
 node(rid,'rule',zh,en,'rules','保留原样的示例 AST；页面不运行原生 Atlas 引擎。','Preserved authored AST. This page does not run native Atlas.',definition_ast=ast,native_compatibility='unverified',namespace='custom_scenario')
 pid=rid.replace('rule.','predicate.')
 node(pid,'predicate',zh,en,'rules','固定规则的可复用命题定义，与某次 True / False / Unknown 结果分离。','Reusable proposition definition, separate from a particular True / False / Unknown result.',native_truth='UNKNOWN',definition_revision='fixture-v1')
 edge(pid,rid,'uses_rule','main');edge(pid,rid,'evaluated_by','fixed_expression')
node('param.threshold','parameter','置信阈值 · 0.85','Confidence threshold · 0.85','rules','仅为演练参数；不是通用诊断、法律或安全阈值。','Scenario-only parameter, not a diagnostic, legal or universal safety threshold.',name='incident_threshold',value=.85,unit='1',scope=NS+'rule.incident')
# Derive visual AST nodes/edges from the source expression, preserving every operand index.
def ast_nodes(rid,expr,path='root'):
 sid=rid+'.ast.'+path
 if 'op' in expr:
  node(sid,'operator',expr['op']+' 运算符',expr['op']+' operator','rules','运算符子节点按 args 原顺序连接。','Ordered argument edges preserve the original AST.',ast_path=path,operator=expr['op'])
  for i,arg in enumerate(expr['args']):
   child=ast_nodes(rid,arg,path+'.'+str(i));edge(sid,child,'has_argument',f'arg[{i}]',argument_index=i,ast_path=path+'.args['+str(i)+']')
 elif 'state' in expr:
  node(sid,'state_ref','state · '+expr['state'].split('.')[-1],'state · '+expr['state'].split('.')[-1],'rules','状态叶节点引用字段规格，不直接含运行值。','State leaf references a specification, not its runtime value.',ast_path=path,state_ref=expr['state']);edge(sid,expr['state'].removeprefix(NS),'references_state','state_leaf',extension=True)
 elif 'param' in expr:
  node(sid,'parameter','param · '+expr['param'],'param · '+expr['param'],'rules','从规则的参数作用域绑定值。','Parameter leaf resolves in the rule scope.',ast_path=path,param=expr['param']);edge(sid,'param.threshold','uses_parameter','lexical_binding')
 elif 'const' in expr:
  node(sid,'constant','const · '+str(expr['const'])+' m','const · '+str(expr['const'])+' m','rules','固定常量和单位保留于源表达式。','Literal value and unit are preserved in source.',ast_path=path,value=expr['const'],unit=expr.get('unit','1'))
 elif 'predicate' in expr:
  node(sid,'target_ref','引用 · '+('疑似跌倒' if 'incident' in expr['predicate'] else '安全距离'),'ref · '+expr['predicate'].split('.')[-1],'rules','引用定义，不能复制成新的实体或把结果写成事实字段。','Reference a definition; do not clone as an entity or treat result as a state field.',ast_path=path,target_ref=expr['predicate']);edge(sid,expr['predicate'].removeprefix(NS),'references_target','predicate')
 return sid
for rid,_,_,ast in rules: edge(rid,ast_nodes(rid,ast),'has_operator','root')
edge('predicate.incident','state.confidence','depends_on','state');edge('predicate.safety','state.separation','depends_on','state')
node('result.incident','predicate_result','疑似跌倒 · TRUE*','Suspected fall · TRUE*','feedback','*作者设定的示例结果，不是原生 Atlas 求值；证明报告置信度条件，不证明真实跌倒。','*Authored illustrative result, not native Atlas evaluation. Supports report confidence, not ground truth.',truth='TRUE',native_truth='UNKNOWN',diagnostic='authored_result_only',query_s=12.1,available_cutoff_s=12.1)
node('result.safety','predicate_result','安全距离 · TRUE*','Safe distance · TRUE*','feedback','*作者设定结果，只在 t=20.1 的输入范围有效；继续执行仍需后续检查。','*Authored result for t=20.1 only. Continued execution requires new checks.',truth='TRUE',native_truth='UNKNOWN',query_s=20.1)
for r,p,f in [('result.incident','predicate.incident','fact.incident'),('result.safety','predicate.safety','fact.separation')]: edge(r,p,'result_of','authored_evaluation',level='runtime');edge(r,f,'based_on','actual_fixture_input',level='runtime')
node('event_def','event_def','新异常报告事件','New incident-report event','rules','事件策略：每个 observation_id 首次从未见变为可用且报告条件为真时记一次；真谓词本身不是事件。','Occurrence policy: once per observation_id when newly available and report condition is true. A true predicate alone is not an event.',occurrence_policy='first_qualified_report_per_observation_id')
node('event','event_record','收到异常报告 @12.1 s','Incident reported @12.1 s','feedback','合成事件记录，参与者 C 与 A，观测编号 obs-001；不是实际儿童事故记录。','Synthetic occurrence with participants C and A, observation obs-001; not a real child incident.',occurrence_s=12.1,observation_id='obs-001')
edge('event_def','predicate.incident','depends_on','qualified_report');edge('event','event_def','instance_of','event_definition',level='runtime');edge('event','result.incident','evidenced_by','qualified_report',level='runtime');edge('event','agent','feeds_back','incident_observation',level='runtime')

# Execution definitions and instances are distinct; composition never mixes levels.
behaviors=[('approach','前往安全观察点','Approach safe point','px4','motion','motion_slot'),('capture','拍摄最小化记录','Capture minimized evidence','perception','capture','camera_slot'),('hold','保持观察位置','Hold observation point','px4','motion','motion_slot'),('upload','上传至演练接收端','Upload to exercise recipient','ns3','upload','radio_slot'),('resume','恢复配送','Resume delivery','px4','motion','motion_slot'),('return','携包裹返航','Return with parcel','px4','motion','motion_slot')]
for cap,zh,en in [('motion','受控运动能力','Controlled motion'),('capture','相机采集能力','Camera capture'),('upload','定向传输能力','Directed transport')]: node('cap.'+cap,'capability',zh,en,'execution','支持这项操作不等于已授权、资源可用或已执行成功。','Support does not establish authorization, availability or success.')
for id,zh,en,unit,capacity in [('motion_slot','唯一运动控制槽','Exclusive motion slot','slot',1),('camera_slot','相机使用槽','Camera-use slot','slot',1),('radio_slot','无线链路容量','Radio-link capacity','Mbit/s',2),('energy_pool','电池能量预算','Battery energy budget','Wh',60)]: node('res.'+id,'resource',zh,en,'execution','容量、预留量和实际用量分别记录；容量不自动证明可用。','Capacity, reservation and measured usage are separate. Capacity alone does not prove availability.',capacity=capacity,unit=unit)
for short,zh,en,mod,cap,res in behaviors:
 node('behavior.'+short,'behavior',zh+' · 定义',en+' · definition','execution','可复用的行为定义；具体运行有独立的执行记录。','Reusable behavior definition, with a separate record for a particular execution.')
 node('exec.'+short,'behavior_execution',zh,en,'execution','本次演练的行为执行；结果由合成回执和事实支持。','Behavior execution in this authored fixture; effects require explicit receipts and facts.',behavior_ref=NS+'behavior.'+short)
 edge('exec.'+short,'behavior.'+short,'instance_of','behavior_definition',level='runtime');edge('behavior.'+short,mod,'executed_by','module_binding');edge('behavior.'+short,'cap.'+cap,'requires_capability','supported_operation');edge('behavior.'+short,'res.'+res,'requires_resource','reserved_capacity',amount=1,unit='Mbit/s' if res=='radio_slot' else 'slot')
 edge('behavior.'+short,'constraint.safety','constrained_by','pre_start_and_continue')
 if short in ['capture','upload']: edge('behavior.'+short,'constraint.privacy','constrained_by','recipient_purpose_minimization')
for order,short in enumerate(['approach','capture','upload']): edge('command','exec.'+short,'composes_behavior','incident_sequence',level='runtime',composition={'mode':'ordered','group':'incident_sequence','order':order})
edge('command','exec.hold','composes_behavior','hold_while_upload',level='runtime',composition={'mode':'parallel','group':'after_capture','with':NS+'exec.upload','start_after':NS+'exec.capture','resource_note':'motion slot and radio capacity are distinct'})
for b,a in [('capture','approach'),('upload','capture'),('hold','capture')]: edge('exec.'+b,'exec.'+a,'waits_for','successful_completion',level='runtime')
for short,condition in [('resume','upload_acknowledged AND delivery_feasible'),('return','upload_failed AND return_feasible')]: edge('command','exec.'+short,'composes_behavior','outcome_branch',level='runtime',composition={'mode':'conditional','group':'post_upload','condition':condition,'unknown_policy':'pause bounded wait; recheck at deadline'})
node('check','constraint_check','启动 + 持续检查','Start + continuation checks','execution','启动与持续执行分别记录检查。UNKNOWN 或缺失权限不放行新拍摄/新上传；可暂停并重新规划。','Start and continuation are distinct checks. UNKNOWN or missing authority does not permit a new capture/upload; pause and replan.',checks=[{'at_s':12.1,'scope':'before_approach','distance_input':'planned corridor, authored fixture','verdict':'PERMIT'},{'at_s':20.1,'scope':'before_capture','fact_ref':NS+'fact.separation','verdict':'PERMIT'},{'at_s':22.1,'scope':'before_upload_and_hold','authorization_ref':NS+'policy','verdict':'PERMIT'}],authority='scenario_start_continue_guard')
node('check_result','check_result','检查放行 · PERMIT*','Check permits · PERMIT*','execution','*合成检查记录，证据范围是指定行为和时段。UNKNOWN 分支暂停新动作。','*Authored check record for named behavior/interval. UNKNOWN pauses new actions.',verdict='PERMIT',unknown_policy='block new actions; pause and replan')
edge('check','constraint.safety','uses_constraint','start_and_continue',level='runtime');edge('check','constraint.privacy','uses_constraint','start_and_continue',level='runtime');edge('check','fact.separation','reads_fact','pre_capture_distance',level='runtime');edge('check','check_result','produces_check_result','authored_guard_result',level='runtime')
for s in ['approach','capture','upload','hold']: edge('check_result','exec.'+s,'permits_or_blocks','start',level='runtime',status='permitted',condition='matching scope and interval only')
node('module_execution','module_execution','模块执行与唯一能量结算','Module execution + energy ledger','execution','声明的模块角色在合成轨迹中输出各自的结果，不代表 PX4 / Gazebo / ns-3 已连接运行。','Declared module roles produce authored trace results. No live PX4 / Gazebo / ns-3 connection is claimed.',ledger={'id':NS+'ledger.001','authority':NS+'energy_module','start_wh':60,'entries':[{'id':'flight-001','wh':3.0},{'id':'camera-001','wh':.04},{'id':'radio-001','wh':.06}],'end_wh':56.9,'charge_rule':'unique entry IDs, once only'})
for f in ['fact.pose','fact.capture','fact.upload','fact.after_energy','fact.paused','fact.target','fact.custody','fact.attachment']: edge('module_execution',f,'produces_fact','authored_module_result',level='runtime')
for mod in ['px4','gazebo','ns3','energy_module','business','perception']: edge('module_execution',mod,'invokes_module','declared_binding',level='runtime',extension=True)
edge('module_execution','res.energy_pool','consumes_resource','energy_accounting',level='runtime',amount=3.1,unit='Wh',ledger_id=NS+'ledger.001',extension=True)
for short in ['approach','capture','upload']: edge('exec.'+short,'module_execution','realized_by','execution_record',level='runtime',extension=True)
node('receipt','receipt','接受 ≠ 应用 ≠ 完成','Accepted ≠ applied ≠ complete','feedback','运动指令在 13 s 接受并替换原目标，观察点在 20 s 到达；拍摄在 22 s 完成；上传必须等接收回执。','Motion accepted at 13 s, replacing the old target; arrival at 20 s, capture complete at 22 s. Upload completion requires an acknowledgement.',records=[{'operation':'approach','accepted_s':13,'applied_s':13.1,'complete_s':20},{'operation':'capture','accepted_s':20.2,'complete_s':22},{'operation':'upload','accepted_s':22.1,'complete_s':38}])
node('evidence','evidence','合成轨迹与来源范围','Synthetic trace + proof scope','feedback','所有数值和回执均为作者设定的夹具。静态演示测试不证明仿真宿主已连接，不证明事故、合法性或真实执行。','All values/receipts are authored fixture records. Static tests prove neither live simulator connection, incident truth, legality nor real execution.',source_kind='authored_fixture',real_host_connected=False,media_present=False,network_upload=False)
edge('receipt','command','receipt_for','command_lifecycle',level='runtime',extension=True);edge('receipt','evidence','evidenced_by','fixture_trace',level='runtime',extension=True)
node('outcome','decision','确认成功后恢复配送','Resume after confirmed success','feedback','成功：38 s 收到合成接收回执，40 s 接受配送目标，45 s 实际向配送点移动；配送未宣称完成。','Success: synthetic ack at 38 s, delivery target accepted at 40 s, actual progress at 45 s. Delivery completion is not claimed.',branch='success',effect_evidence={'at_s':45,'task_state':'active','accepted_target':'fictional dropoff D','actual_pose_m':[40,12,15],'frame':'local_ENU','custodian':NS+'uav','parcel_attached':True})
edge('fact.upload','agent','feeds_back','transport_result',level='runtime');edge('receipt','agent','feeds_back','command_lifecycle',level='runtime',extension=True);edge('fact.after_energy','agent','feeds_back','energy_budget',level='runtime');edge('outcome','exec.resume','selects_branch','receipt_and_feasibility',level='runtime',extension=True);edge('outcome','evidence','evidenced_by','recorded_effect',level='runtime',extension=True)
# Physical carrying and accountable custody are separate relations between the same pair.
edge('uav','parcel','physical_attachment','carrier_to_payload',level='runtime',evidence=NS+'fact.attachment',extension=True)
edge('uav','parcel','responsible_custody','custodian_to_parcel',level='runtime',evidence=NS+'fact.custody',extension=True)
edge('camera','res.camera_slot','provides_resource','device_capacity',extension=True);edge('radio','res.radio_slot','provides_resource','link_capacity',extension=True)
edge('exec.upload','endpoint','sends_to','scenario_authorized_recipient',level='runtime',payload='synthetic metadata only',actual_transmission=False,extension=True)

from add_airspace import extend
airspace_branches=extend(NS,node,edge,N,E,kinds)
from coverage import add_relations, inventory
add_relations(NS,node,edge,N,E,kinds)

D={
 'schema_version':'aeroagentsim.scenario-graph-projection/v1',
 'title':{'zh':'一次配送，如何因异常重新规划','en':'How an incident changes a delivery'},
 'namespace':NS,'provenance':{'kind':'authored_synthetic_fixture','native_catalog_revision':None,'native_engine_executed':False,'real_host_connected':False,'legal_source_verified':False,'actual_upload':False},
 'kind_registry':{k:{'zh':v[0],'en':v[1],'family':v[2],'workbench_collection':v[3]} for k,v in kinds.items()},
 'nodes':N,'edges':E,
 'mapping':{'target_schema':'aeroagentsim.unified-graph/v1','mode':'read_only_projection_not_importable_authoring_config','compatible_concepts':'Node kind registry maps supported definition kinds to workbench collections. IDs use a custom scenario namespace. State specs map to fields. Derived AST nodes come only from definition_ast.','runtime_separation':'Command, behavior execution, strategy invocation, result, receipt and event occurrence are authored trace records. They must not be imported as reusable definitions. Native predicate truth remains UNKNOWN.','extensions':'Edges marked extension are declared display-level scenario relations, not claimed native exporter enums. Projection validation does not establish workbench or Atlas runtime compatibility.','regulatory_source':'Not applicable: no law or legal clause is asserted. Exercise policy is explicitly separate.'},
 'airspace_branches':airspace_branches,
 'branches':{
  'success':{'zh':'成功后恢复配送','en':'Resume after success','upload_status':'acknowledged','truth':'TRUE','at_s':38,'latency_s':16,'decision_zh':'确认成功后恢复配送','decision_en':'Resume after confirmed success','description_zh':'38 s 收到合成接收回执 → 40 s 接受配送目标 → 45 s 实际朝配送点移动。原任务恢复，包裹仍挂载；不宣称已完成配送。','description_en':'Synthetic ack at 38 s → delivery target accepted at 40 s → actual progress at 45 s. Original task resumes; parcel remains attached. Delivery not complete.','effect':{'at_s':45,'task_state':'active','accepted_target':'dropoff D','actual_pose_m':[40,12,15],'parcel_attached':True,'custodian':NS+'uav','receipt':'resume-complete-001'},'selected_execution':NS+'exec.resume'},
  'failure':{'zh':'失败后携包裹返航','en':'Return after failure','upload_status':'failed','truth':'FALSE','at_s':42,'latency_s':20,'decision_zh':'上传失败，携包裹返航','decision_en':'Upload failed; return with parcel','description_zh':'42 s 收到失败回执，43 s 收到新返航批准，44 s 新时隙开始后再进入反向航道；78 s 合成轨迹到达基地，包裹仍由 A 保管和挂载。配送没有恢复或完成。','description_en':'Failure at 42 s; a separate return grant arrives at 43 s. Reverse transit begins only at 44 s. Authored pose reaches base at 78 s. Parcel remains attached and in A custody. Delivery neither resumes nor completes.','effect':{'at_s':78,'task_state':'paused_returned','accepted_target':'base B','actual_pose_m':[0,0,0],'parcel_attached':True,'custodian':NS+'uav','receipt':'return-arrival-001','allocation_ref':NS+'return_allocation','grant_receipt_ref':NS+'return_grant_receipt','actual_samples':[{'t_s':44,'pose_m':[35,12,15]},{'t_s':46,'pose_m':[30,15,15]},{'t_s':60,'pose_m':[0,15,15]},{'t_s':77.9,'pose_m':[0,0,15]},{'t_s':78,'pose_m':[0,0,0]}]},'selected_execution':NS+'exec.return'},
  'unknown':{'zh':'回执未知，限时等待','en':'Unknown receipt; bounded wait','upload_status':'unknown','truth':'UNKNOWN','at_s':46,'latency_s':24,'decision_zh':'未知结果，暂停并限时等待','decision_en':'Unknown; pause and await','description_zh':'46 s 仍无可信接收回执。配送保持暂停，持位继续接受安全检查；到 50 s 必须重新评估并选择安全后续。本快照不宣称上传、恢复或返航完成。','description_en':'No trustworthy acknowledgement at 46 s. Delivery remains paused; hold continues under safety checks. Reevaluate at the 50 s deadline. This snapshot claims no upload, resume or return completion.','effect':{'at_s':46,'task_state':'paused','accepted_target':'safe observation point','actual_pose_m':[35,12,15],'parcel_attached':True,'custodian':NS+'uav','receipt':None,'wait_deadline_s':50},'selected_execution':NS+'exec.hold'}
 },
 'view_definitions':{
 'loop':{'zh':'主闭环','en':'Main loop','description_zh':'从左向右读：观测和约束 → 代理决策 → 检查与行为 → 新事实 → 再决策。选择节点查看所有关联，切到完整图查看每个类型。','description_en':'Read left to right: observations + constraints → agent decision → checks + behavior → new facts → replanning. Select a node for all relations; Full graph includes every type.','nodes':['uav','parcel','fact.incident','fact.energy','event','constraint.incident','constraint.safety','constraint.privacy','goal','agent','strategy','decision','command','check','check_result','exec.approach','exec.capture','exec.upload','module_execution','fact.upload','receipt','outcome','fact.custody','task']},
 'ast':{'zh':'规则与 AST','en':'Rules + AST','description_zh':'运算符指向有序参数。状态叶引用字段；运行事实和本次结果是不同节点。所有表达式由示例 AST 自动展开。','description_en':'Operators point to ordered arguments. State leaves reference fields; facts and results remain separate. All expression nodes are derived from the authored AST.'},
 'execution':{'zh':'行为与资源','en':'Behaviors + resources','description_zh':'指令按顺序/并行/条件组合行为。单一运动槽串行；相机和无线电是不同资源。资源、权限、可行性分别检查。','description_en':'Commands compose ordered, parallel and conditional behavior. One motion slot stays exclusive; camera and radio are separate resources.'},
 'facts':{'zh':'事实与来源','en':'Facts + provenance','description_zh':'实体拥有字段，模块拥有计算权，代理拥有指令权限。三者不是同一种关系。包裹挂载与责任保管也是两条边。','description_en':'Entity field ownership, module computing authority and agent command authority are different relations. Parcel attachment and custody are different edges.'},
 'all':{'zh':'完整类型图','en':'Full type graph','description_zh':'完整图保留所有节点与多重边。滚轮缩放、拖动平移；悬停突出一跳关联，点击固定细节。','description_en':'Every node and multiedge is retained. Wheel to zoom and drag to pan; hover highlights neighbors, click pins detail.'}
 }
}
D['view_definitions']['loop']['nodes']=['uav','parcel','person','no_fly','corridor','agent','perception','fact.incident','request','report_receipt','edge_server','edge_agent','edge_strategy','approval','allocation','grant_receipt','check','check_result','strategy','decision','command','exec.approach','exec.capture','exec.upload','module_execution','fact.upload','outcome','receipt','fact.custody','task']
D['view_definitions']['airspace']={'zh':'空域与时隙','en':'Airspace + time slots','description_zh':'无人机计算发现异常 → 通信上报申请 → 边缘代理审批分配时隙 → 批准送达 → 三重门检查。禁飞区、航道时窗与实际运动分别建模。','description_en':'UAV computes an incident observation → communication reports and requests → edge agent approves and allocates time → grant delivered → three admission gates. No-fly zones, leases and actual motion remain distinct.'}
D['view_definitions']['loop']['description_zh']='从左向右读：无人机计算发现并上报 → 边缘代理批准分时航道 → 收到授权并检查 → 绕飞拍摄上传 → 用真实可用的示例回执再规划。'
D['view_definitions']['loop']['description_en']='Read left to right: UAV computes and reports → edge agent grants a corridor time slot → received authority and checks → detour, capture, upload → replan from available fixture receipts.'
for branch_id,entries in {'success':[('outbound-flight',3.0),('camera',.04),('radio',.06),('resumed-flight',2.0)],'failure':[('outbound-flight',3.0),('camera',.04),('radio-failed',.08),('return-flight',5.0)],'unknown':[('outbound-flight',3.0),('camera',.04),('radio-pending',.09),('continued-hold',.8)]}.items():
 D['branches'][branch_id]['energy_ledger']={'id':NS+'ledger.'+branch_id,'authority':NS+'energy_module','start_wh':60,'entries':[{'id':branch_id+'.'+i,'wh':v} for i,v in entries],'end_wh':round(60-sum(v for _,v in entries),2),'at_s':D['branches'][branch_id]['effect']['at_s'],'charge_rule':'each distinct entry exactly once; do not add separate module charges again'}
# Explicit source/target signatures for the scenario extensions.
D['relation_registry']={}
for e in E:
 r=D['relation_registry'].setdefault(e['type'],{'extension':e.get('extension',False),'signatures':[]})
 sig=[next(n['kind'] for n in N if n['id']==e['source']),next(n['kind'] for n in N if n['id']==e['target'])]
 if sig not in r['signatures']:r['signatures'].append(sig)
D['coverage']={'node_count':len(N),'edge_count':len(E),'kind_count':len(set(n['kind'] for n in N)),'relation_count':len(D['relation_registry']),'claim':'Representative established concept families; not a verified full native catalog or operator inventory.'}
D['coverage_inventory']=inventory(D)
(ROOT/'emergency-delivery-graph.coverage.json').write_text(json.dumps(D['coverage_inventory'],ensure_ascii=False,indent=2)+'\n')
(ROOT/'emergency-delivery-graph.fixture.json').write_text(json.dumps(D,ensure_ascii=False,indent=2)+'\n')
template=(Path(__file__).parent/'page.template.html').read_text()
(ROOT/'emergency-delivery-graph.html').write_text(template.replace('__GRAPH_JSON__',json.dumps(D,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')))
print(D['coverage'])
