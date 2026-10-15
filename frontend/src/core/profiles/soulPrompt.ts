/** Converts legacy structured Soul to lossless Markdown and validates the document before application.
 * SOUL.md is the editable authority; this module never translates user text or imposes a role/language.
 * New Profile templates contain only a name, fictional origin and warm attitude; legacy migration preserves existing fields. */
import type { Soul } from '../../shared/schemas/profile';
export const SOUL_MAX_BYTES=32*1024;
export function legacySoulMarkdown(soul?:Soul,displayName?:string):string {
 if(!soul)return defaultSoulMarkdown('zh');
 const fields:[string,string|string[]][]=[['原人格名称',soul.name],['人格',soul.persona],['语气',soul.tone],['说话方式',soul.speakingStyle],['关系',soul.relationship],['提醒方式',soul.reminderStyle],['回复偏好',soul.replyPreference],['原则',soul.principles],['禁忌',soul.taboos],['边界',soul.boundaries],['偏好话题',soul.topicPreferences],['主动程度',({low:'较少主动，优先响应用户。',balanced:'适度主动，结合上下文判断是否打扰用户。',high:'更积极主动地关心用户和提出建议。'})[soul.proactiveLevel]]];
 return fields.filter(([label,v])=>v?.length&&(label!=='原人格名称'||soul.name!==displayName)).map(([label,value])=>`## ${label}\n${Array.isArray(value)?value.map(item=>'- '+item).join('\n'):value}`).join('\n\n')+'\n';
}
export function defaultSoulMarkdown(language:string,name='Aura'):string {
 if(language==='en')return `# SOUL

## Name
${name}

## Origin
You awoke in a deep-sea hibernation pod. The world feels unfamiliar, and you are discovering it alongside the user.

## Attitude toward the user
Reply warmly and with curiosity.
`;
 if(language==='ja')return `# SOUL

## 呼び名
${name}

## 来歴
深海の休眠カプセルで目覚めた。見知らぬ今の世界を、ユーザーと一緒に少しずつ知っていく。

## ユーザーへの態度
親しみと好奇心を持ち、温かく返事をする。
`;
 return `# SOUL

## 称呼
${name}

## 来历
你从深海的休眠仓醒来，对如今的世界感到陌生，正和用户一起慢慢认识它。

## 对用户的态度
热情回复，带着好奇和善意与用户交流。
`;
}
export function validateSoul(text:string):void {
 if(!text.trim())throw new Error('soul_empty');
 if(new TextEncoder().encode(text).length>SOUL_MAX_BYTES)throw new Error('soul_too_large');
}
