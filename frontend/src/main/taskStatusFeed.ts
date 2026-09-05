/** Reads Backend Task snapshots and one cursor-based SSE stream for a visible console subscription.
 * Each subscription owns its abort signal, timers and serialized refreshes, so stopped work cannot
 * publish or continue paging. Only the newest 100 current-profile title/status rows are retained;
 * SSE payload bodies are discarded, and this module never controls Task lifecycle or persists state.
 */
import type { TaskSummary } from '../shared/contracts/desktop';
import { backendJson, BACKEND_URL } from './backendProcess';

interface Subscription {
  controller:AbortController;
  cursor:string;
  refresh?:Promise<void>;
  refreshAgain:boolean;
  refreshTimer?:ReturnType<typeof setTimeout>;
}

export class TaskStatusFeed {
  private subscription?:Subscription;
  constructor(private profile:()=>string,private changed:(value:{tasks:TaskSummary[];error?:string})=>void) {}

  start() {
    if(this.subscription)return;
    const subscription:Subscription={controller:new AbortController(),cursor:'0',refreshAgain:false};
    this.subscription=subscription;
    void this.run(subscription);
  }
  stop() {
    const subscription=this.subscription;
    if(!subscription)return;
    this.subscription=undefined;
    clearTimeout(subscription.refreshTimer);
    subscription.refreshTimer=undefined;
    subscription.controller.abort();
  }
  private current(subscription:Subscription) {
    return this.subscription===subscription&&!subscription.controller.signal.aborted;
  }
  private async readSnapshot(subscription:Subscription) {
    const profileId=this.profile();
    const records:TaskSummary[]=[];
    let cursor:number|undefined;
    do {
      if(!this.current(subscription))return;
      const query=new URLSearchParams({ownerProfileId:profileId,limit:'200'});
      if(cursor!==undefined)query.set('cursor',String(cursor));
      const page=await backendJson('/api/v1/tasks?'+query,{signal:subscription.controller.signal});
      if(!this.current(subscription))return;
      if(this.profile()!==profileId){subscription.refreshAgain=true;return;}
      for(const item of page.items)records.push({id:item.id,title:item.title,status:item.status});
      if(records.length>100)records.splice(0,records.length-100);
      cursor=page.nextCursor??undefined;
    }while(cursor!==undefined);
    if(this.current(subscription))this.changed({tasks:records.reverse()});
  }
  private refresh(subscription:Subscription):Promise<void> {
    if(!this.current(subscription))return Promise.resolve();
    if(subscription.refresh){subscription.refreshAgain=true;return subscription.refresh;}
    subscription.refresh=(async()=>{
      do {
        subscription.refreshAgain=false;
        await this.readSnapshot(subscription);
      }while(this.current(subscription)&&subscription.refreshAgain);
    })().finally(()=>{subscription.refresh=undefined;});
    return subscription.refresh;
  }
  private scheduleRefresh(subscription:Subscription) {
    if(!this.current(subscription))return;
    if(subscription.refresh){subscription.refreshAgain=true;return;}
    if(subscription.refreshTimer)return;
    subscription.refreshTimer=setTimeout(()=>{
      subscription.refreshTimer=undefined;
      void this.refresh(subscription).catch(()=>{
        if(this.current(subscription))this.changed({tasks:[],error:'Task 状态读取失败'});
      });
    },200);
  }
  private async readEvents(subscription:Subscription,response:Response) {
    if(!response.ok||!response.body)throw new Error('Task 状态不可用');
    const reader=response.body.getReader();
    const decoder=new TextDecoder();
    const cancel=()=>{void reader.cancel().catch(()=>undefined);};
    subscription.controller.signal.addEventListener('abort',cancel,{once:true});
    let line='',eventId='',eventType='';
    try {
      while(this.current(subscription)) {
        const chunk=await reader.read();
        if(!this.current(subscription)||chunk.done)break;
        const lines=decoder.decode(chunk.value,{stream:true}).split('\n');
        for(let index=0;index<lines.length;index++) {
          // Only id/event headers matter; never retain arbitrarily large data lines or events.
          line=(line+lines[index]).slice(0,256);
          if(index===lines.length-1)break;
          const complete=line.replace(/\r$/,'');line='';
          if(!complete) {
            if(eventId)subscription.cursor=eventId;
            if(/^task\.(created|status_changed|deleted)$/.test(eventType))this.scheduleRefresh(subscription);
            eventId='';eventType='';
          }else if(/^id: \d+$/.test(complete))eventId=complete.slice(4);
          else if(complete.startsWith('event: '))eventType=complete.slice(7);
        }
      }
    } finally {
      subscription.controller.signal.removeEventListener('abort',cancel);
      await reader.cancel().catch(()=>undefined);
      reader.releaseLock();
    }
  }
  private async run(subscription:Subscription) {
    let retry=1000;
    while(this.current(subscription)) {
      try {
        await this.refresh(subscription);
        if(!this.current(subscription))return;
        const response=await fetch(BACKEND_URL+'/api/v1/task-events',{
          headers:{'Last-Event-ID':subscription.cursor},signal:subscription.controller.signal,
        });
        if(!this.current(subscription)){await response.body?.cancel().catch(()=>undefined);return;}
        if(response.ok&&response.body)retry=1000;
        await this.readEvents(subscription,response);
      }catch{
        if(this.current(subscription))this.changed({tasks:[],error:'Task 状态暂时不可用，正在重新连接。'});
      }
      if(this.current(subscription))await new Promise<void>(resolve=>{
        const signal=subscription.controller.signal;
        const done=()=>{clearTimeout(timer);signal.removeEventListener('abort',done);resolve();};
        const timer=setTimeout(done,retry);
        signal.addEventListener('abort',done,{once:true});
      });
      retry=Math.min(retry*2,10000);
    }
  }
}
