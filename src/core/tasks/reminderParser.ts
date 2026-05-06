const RELATIVE_TIME_PATTERN =
  /(?<amount>\d+(?:\.\d+)?|半|[零一二两三四五六七八九十百千万]+)\s*(?:个)?\s*(?<unit>分钟|分|小时|钟头|天|日|周|星期|个月)后/;
const REMINDER_HINT_PATTERN = /(提醒我|记得提醒我|别忘了提醒我|别忘提醒我|到时候提醒我|提醒一下我)/;

export interface ReminderParseInput {
  message: string;
  nowIso?: string;
}

export interface ParsedReminderRequest {
  content: string;
  dueAt: string;
}

export function isReminderRequest(message: string): boolean {
  return REMINDER_HINT_PATTERN.test(message) && RELATIVE_TIME_PATTERN.test(message);
}

export function parseReminderRequest(input: ReminderParseInput): ParsedReminderRequest | undefined {
  const message = input.message.trim();

  if (!isReminderRequest(message)) {
    return undefined;
  }

  const relativeTime = message.match(RELATIVE_TIME_PATTERN);
  const baseTime = new Date(input.nowIso ?? new Date().toISOString());

  if (!relativeTime?.groups || !Number.isFinite(baseTime.getTime())) {
    return undefined;
  }

  const offsetMs = parseRelativeOffsetMs(relativeTime.groups.amount, relativeTime.groups.unit);

  if (!offsetMs || offsetMs <= 0) {
    return undefined;
  }

  return {
    content: cleanupReminderContent(message.replace(RELATIVE_TIME_PATTERN, "")) || "这件事",
    dueAt: new Date(baseTime.getTime() + offsetMs).toISOString()
  };
}

function parseRelativeOffsetMs(amountText: string, unit: string): number | undefined {
  const amount = parseChineseNumberish(amountText);

  if (!amount || amount <= 0) {
    return undefined;
  }

  if (unit === "分钟" || unit === "分") {
    return amount * 60_000;
  }

  if (unit === "小时" || unit === "钟头") {
    return amount * 60 * 60_000;
  }

  if (unit === "天" || unit === "日") {
    return amount * 24 * 60 * 60_000;
  }

  if (unit === "周" || unit === "星期") {
    return amount * 7 * 24 * 60 * 60_000;
  }

  if (unit === "个月") {
    return amount * 30 * 24 * 60 * 60_000;
  }

  return undefined;
}

function parseChineseNumberish(value: string): number | undefined {
  if (/^\d+(?:\.\d+)?$/.test(value)) {
    return Number(value);
  }

  if (value === "半") {
    return 0.5;
  }

  const digits: Record<string, number> = {
    零: 0,
    一: 1,
    二: 2,
    两: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9
  };
  const units: Record<string, number> = {
    十: 10,
    百: 100,
    千: 1000,
    万: 10000
  };
  let total = 0;
  let current = 0;

  for (const character of value) {
    if (character in digits) {
      current = digits[character];
      continue;
    }

    const unit = units[character];

    if (!unit) {
      return undefined;
    }

    if (unit === 10000) {
      total = (total + current) * unit;
      current = 0;
      continue;
    }

    total += (current || 1) * unit;
    current = 0;
  }

  return total + current;
}

function cleanupReminderContent(value: string): string {
  return value
    .replace(REMINDER_HINT_PATTERN, "")
    .replace(/^(我|一下|一下我|到时候|记得|别忘了|别忘)/u, "")
    .replace(/[，。,.！？!?；;\s]+$/gu, "")
    .trim();
}
