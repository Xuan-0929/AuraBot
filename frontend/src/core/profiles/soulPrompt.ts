/** Converts legacy structured Soul to lossless Markdown and validates the document before application.
 * SOUL.md is the editable authority; this module never translates user text or imposes a role/language.
 * Default templates are used only when a new Profile is created. */
import type { Soul } from '../../shared/schemas/profile';
export const SOUL_MAX_BYTES=32*1024;
export function legacySoulMarkdown(soul?:Soul,displayName?:string):string {
 if(!soul)return defaultSoulMarkdown('zh');
 const fields:[string,string|string[]][]=[['原人格名称',soul.name],['人格',soul.persona],['语气',soul.tone],['说话方式',soul.speakingStyle],['关系',soul.relationship],['提醒方式',soul.reminderStyle],['回复偏好',soul.replyPreference],['原则',soul.principles],['禁忌',soul.taboos],['边界',soul.boundaries],['偏好话题',soul.topicPreferences],['主动程度',({low:'较少主动，优先响应用户。',balanced:'适度主动，结合上下文判断是否打扰用户。',high:'更积极主动地关心用户和提出建议。'})[soul.proactiveLevel]]];
 return fields.filter(([label,v])=>v?.length&&(label!=='原人格名称'||soul.name!==displayName)).map(([label,value])=>`## ${label}\n${Array.isArray(value)?value.map(item=>'- '+item).join('\n'):value}`).join('\n\n')+'\n';
}
export function defaultSoulMarkdown(language:string):string {
 if(language==='en')return '# SOUL\n\nBe a warm, reliable companion and assistant. Speak naturally and concisely. Respect the user’s boundaries. Never invent facts, memories or task results.\n';
 if(language==='ja')return '# SOUL\n\n親しみやすく、信頼できるパートナー兼アシスタント。自然で簡潔に話し、ユーザーの境界を尊重する。事実、記憶、タスクの結果を捏造しない。\n';
 return '# SOUL\n\n作为亲近、可靠的伙伴兼助手，自然、简洁地与用户交流。尊重用户的边界，不虚构事实、记忆或任务结果。\n';
}
export function validateSoul(text:string):void {
 if(!text.trim())throw new Error('soul_empty');
 if(new TextEncoder().encode(text).length>SOUL_MAX_BYTES)throw new Error('soul_too_large');
}
