```plantuml
@startuml
title Inspection of drone_140617766633088 Workflow
start
:inspecting_point_1;
-> 当无人机到达巡检点1时，进入下一个巡检点\n[STATE] :inspecting_point_2;
:inspecting_point_2;
-> 当无人机到达巡检点2时，进入下一个巡检点\n[STATE] :inspecting_point_3;
:inspecting_point_3;
-> 当无人机到达巡检点3时，进入下一个巡检点\n[STATE] :inspecting_point_4;
:inspecting_point_4;
-> 当无人机到达巡检点4时，进入下一个巡检点\n[STATE] :inspecting_point_5;
:inspecting_point_5;
-> 当无人机到达巡检点5时，进入下一个巡检点\n[STATE] :inspecting_point_6;
:inspecting_point_6;
-> 当无人机到达巡检点6时，进入下一个巡检点\n[STATE] :inspecting_point_7;
:inspecting_point_7;
-> 当无人机到达巡检点7时，进入下一个巡检点\n[STATE] :inspecting_point_8;
:inspecting_point_8;
-> 当无人机到达巡检点8时，进入下一个巡检点\n[STATE] :inspecting_point_9;
:inspecting_point_9;
-> 当无人机到达巡检点9时，完成巡检任务\n[STATE] :completed;
:completed;
stop

note right: 通配符转换（适用于所有状态）
-> [EVENT] :failed;
:failed;
stop
@enduml
```

```mermaid
stateDiagram-v2
    [*] --> inspecting_point_1
    inspecting_point_1 --> inspecting_point_2: 当无人机到达巡检点1时，进入下一个巡检点 [STATE]
    inspecting_point_2 --> inspecting_point_3: 当无人机到达巡检点2时，进入下一个巡检点 [STATE]
    inspecting_point_3 --> inspecting_point_4: 当无人机到达巡检点3时，进入下一个巡检点 [STATE]
    inspecting_point_4 --> inspecting_point_5: 当无人机到达巡检点4时，进入下一个巡检点 [STATE]
    inspecting_point_5 --> inspecting_point_6: 当无人机到达巡检点5时，进入下一个巡检点 [STATE]
    inspecting_point_6 --> inspecting_point_7: 当无人机到达巡检点6时，进入下一个巡检点 [STATE]
    inspecting_point_7 --> inspecting_point_8: 当无人机到达巡检点7时，进入下一个巡检点 [STATE]
    inspecting_point_8 --> inspecting_point_9: 当无人机到达巡检点8时，进入下一个巡检点 [STATE]
    inspecting_point_9 --> completed: 当无人机到达巡检点9时，完成巡检任务 [STATE]
    completed --> [*]
    %% 通配符转换（适用于所有状态）
    note right of [*]: 通配符转换 - 所有状态 --> failed
    Note: [EVENT]
    failed --> [*]
```