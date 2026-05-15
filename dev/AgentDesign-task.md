# 任务型Agent设计

任务型agent不需要自己的soul等，是一个即创即销毁的运行时Agent。

任务型Agent受到Chat agent的调度。
- chat agent在调度是，传入参数，要求任务型agent为plan-build模式还是基础的re-act模式。
- 任务型agent会接受chat agent的打断。
- 任务型agent的skill和tool由chat agent决定。
- 任务型agent持有的用户记忆在chat指派时以prompt形式传递。
- 任务型agent无法自己访问记忆库。记忆的写入需在返回给chat的response中的概要中体现。

