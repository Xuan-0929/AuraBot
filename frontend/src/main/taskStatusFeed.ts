/** Delivers Backend's current-profile Task snapshot SSE to the visible console.
 * Each subscription owns one request and retry lifetime; cancellation and profile checks prevent
 * stale publication. Snapshots replace at most 100 title/status rows without REST reads or replay.
 */
import type { TaskSummary } from '../shared/contracts/desktop';
import { BACKEND_URL } from './backendProcess';

export class TaskStatusFeed {
  private controller?: AbortController;
  constructor(private profile:()=>string,private changed:(value:{tasks:TaskSummary[];error?:string})=>void) {}

  start() {
    if(this.controller)return;
    this.controller=new AbortController();
    void this.run(this.controller);
  }
  stop() {
    this.controller?.abort();
    this.controller=undefined;
  }
  private current(controller:AbortController) {
    return this.controller===controller&&!controller.signal.aborted;
  }
  private async readSnapshots(controller:AbortController,profileId:string,response:Response) {
    if(!response.ok||!response.body)throw new Error('Task 状态不可用');
    const reader=response.body.getReader();
    const decoder=new TextDecoder();
    const cancel=()=>{void reader.cancel().catch(()=>undefined);};
    controller.signal.addEventListener('abort',cancel,{once:true});
    let pending='',eventType='',data='';
    try {
      while(this.current(controller)) {
        const chunk=await reader.read();
        if(!this.current(controller)||this.profile()!==profileId||chunk.done)return;
        pending+=decoder.decode(chunk.value,{stream:true});
        let newline:number;
        while((newline=pending.indexOf('\n'))!==-1) {
          const line=pending.slice(0,newline).replace(/\r$/,'');
          pending=pending.slice(newline+1);
          if(!line) {
            if(eventType==='task.snapshot') {
              const snapshot:unknown=JSON.parse(data);
              if(!snapshot||typeof snapshot!=='object'||!('tasks' in snapshot)||!Array.isArray(snapshot.tasks))throw new Error('Invalid Task snapshot');
              const tasks:TaskSummary[]=snapshot.tasks.slice(0,100).map(item=>{
                if(!item||typeof item.id!=='string'||typeof item.title!=='string'||typeof item.status!=='string')throw new Error('Invalid Task row');
                return {id:item.id,title:item.title,status:item.status};
              });
              if(!this.current(controller)||this.profile()!==profileId)return;
              this.changed({tasks});
            }
            eventType='';data='';
          }else if(line.startsWith('event:'))eventType=line.slice(6).replace(/^ /,'');
          else if(line.startsWith('data:'))data+=line.slice(5).replace(/^ /,'')+'\n';
          if(data.length>1_048_576)throw new Error('Task snapshot too large');
        }
        if(pending.length>1_048_576)throw new Error('Task snapshot too large');
      }
    } finally {
      controller.signal.removeEventListener('abort',cancel);
      await reader.cancel().catch(()=>undefined);
      reader.releaseLock();
    }
  }
  private async run(controller:AbortController) {
    let retry=1000;
    while(this.current(controller)) {
      const profileId=this.profile();
      try {
        const query=new URLSearchParams({ownerProfileId:profileId});
        const response=await fetch(BACKEND_URL+'/api/v1/task-events?'+query,{signal:controller.signal});
        if(!this.current(controller)||this.profile()!==profileId){await response.body?.cancel().catch(()=>undefined);if(!this.current(controller))return;continue;}
        if(response.ok&&response.body)retry=1000;
        await this.readSnapshots(controller,profileId,response);
      }catch{
        if(this.current(controller)&&this.profile()===profileId)this.changed({tasks:[],error:'Task 状态暂时不可用，正在重新连接。'});
      }
      if(this.current(controller))await new Promise<void>(resolve=>{
        const done=()=>{clearTimeout(timer);controller.signal.removeEventListener('abort',done);resolve();};
        const timer=setTimeout(done,retry);
        controller.signal.addEventListener('abort',done,{once:true});
      });
      retry=Math.min(retry*2,10000);
    }
  }
}
