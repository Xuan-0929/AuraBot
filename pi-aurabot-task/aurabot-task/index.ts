/**
 * 注册 AuraBot Task Package 提供的 Pi Extension 能力。
 *
 * 这是 manifest 指向的唯一入口，目前只把 ask_main_agent 注册给 Pi Agent Loop；
 * Tool 的交互细节独立在 askMainAgent.ts，入口不启动后台资源或建立 AuraBot 协议层。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import { createAskMainAgentTool } from "./askMainAgent.ts";

export default function registerAuraBotTask(pi: ExtensionAPI): void {
  pi.registerTool(createAskMainAgentTool());
}
