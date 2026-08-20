/**
 * 定义 AuraBot Task 向 Main Agent 请求业务信息的唯一 Pi-native Tool。
 *
 * Tool 通过 Pi 的标准 Extension UI 发起 input/select dialog，并在标题中加入稳定
 * marker，供 Python RPC 层映射为 TaskQuestion；它不实现权限审批、网络协议或持久化。
 */

import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export const AURABOT_TASK_QUESTION_MARKER = "AURABOT_TASK_QUESTION_V1\n";

const AskMainAgentParameters = Type.Object({
  question: Type.String({ minLength: 1, description: "Business question requiring an answer." }),
  options: Type.Optional(
    Type.Array(Type.String({ minLength: 1 }), {
      minItems: 1,
      description: "Optional choices presented to the Main Agent.",
    }),
  ),
});

type AskMainAgentDetails =
  | { answer: string }
  | { cancelled: true };

function cancelledResult() {
  return {
    isError: true,
    content: [{ type: "text" as const, text: "Main Agent question was cancelled." }],
    details: { cancelled: true as const },
  };
}

export function createAskMainAgentTool(): ToolDefinition<
  typeof AskMainAgentParameters,
  AskMainAgentDetails
> {
  return {
    name: "ask_main_agent",
    label: "Ask Main Agent",
    description:
      "Ask AuraBot's Main Agent for missing business information required to finish the Task.",
    promptSnippet: "Ask the Main Agent only when required business information is missing.",
    parameters: AskMainAgentParameters,
    executionMode: "sequential",
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      if (signal?.aborted) {
        return cancelledResult();
      }

      const title = `${AURABOT_TASK_QUESTION_MARKER}${params.question}`;
      const options = { signal };
      const answer = params.options?.length
        ? await ctx.ui.select(title, params.options, options)
        : await ctx.ui.input(title, "请输入回答", options);

      if (signal?.aborted || answer === undefined) {
        return cancelledResult();
      }

      return {
        isError: false,
        content: [{ type: "text", text: `Main Agent answered: ${answer}` }],
        details: { answer },
      };
    },
  };
}
