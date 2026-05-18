const RELATIVE_TIME_PATTERN =
  /(?<amount>\d+(?:\.\d+)?|半|[零一二两三四五六七八九十百千万]+)\s*(?:个)?\s*(?<unit>分钟|分|小时|钟头|天|日|周|星期|个月)后/;
const ABSOLUTE_DAY_TIME_PATTERN =
  /(?<day>今天|明天|后天|大后天)?\s*(?<period>凌晨|早上|上午|中午|下午|晚上|今晚)?\s*(?<hour>\d{1,2}|[零一二两三四五六七八九十]+)\s*(?:点|:|：)\s*(?<minute>\d{1,2}|[零一二两三四五六七八九十]+)?\s*(?:分)?/;
const VAGUE_LATER_PATTERN = /(稍后|晚点|过会儿|一会儿)/;
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
  return REMINDER_HINT_PATTERN.test(message) && (RELATIVE_TIME_PATTERN.test(message) || ABSOLUTE_DAY_TIME_PATTERN.test(message) || VAGUE_LATER_PATTERN.test(message));
}

export function parseReminderRequest(input: ReminderParseInput): ParsedReminderRequest | undefined {
  const message = input.message.trim();

  if (!isReminderRequest(message)) {
    return undefined;
  }

  const baseTime = new Date(input.nowIso ?? new Date().toISOString());

  if (!Number.isFinite(baseTime.getTime())) {
    return undefined;
  }

  const relativeTime = message.match(RELATIVE_TIME_PATTERN);
  if (relativeTime?.groups) {
    const offsetMs = parseRelativeOffsetMs(relativeTime.groups.amount, relativeTime.groups.unit);

    if (!offsetMs || offsetMs <= 0) {
      return undefined;
    }

    return {
      content: cleanupReminderContent(message.replace(RELATIVE_TIME_PATTERN, "")) || "这件事",
      dueAt: new Date(baseTime.getTime() + offsetMs).toISOString()
    };
  }

  const vagueLater = message.match(VAGUE_LATER_PATTERN);
  if (vagueLater) {
    return {
      content: cleanupReminderContent(message.replace(VAGUE_LATER_PATTERN, "")) || "这件事",
      dueAt: new Date(baseTime.getTime() + 10 * 60_000).toISOString()
    };
  }

  const absoluteTime = message.match(ABSOLUTE_DAY_TIME_PATTERN);
  const dueAt = absoluteTime?.groups ? parseAbsoluteDayTime(absoluteTime.groups, input.nowIso, baseTime) : undefined;

  if (!dueAt) {
    return undefined;
  }

  return {
    content: cleanupReminderContent(message.replace(ABSOLUTE_DAY_TIME_PATTERN, "")) || "这件事",
    dueAt
  };
}

function parseAbsoluteDayTime(
  groups: Record<string, string | undefined>,
  nowIso: string | undefined,
  baseTime: Date
): string | undefined {
  const hour = parseChineseNumberish(groups.hour ?? "");
  const minute = groups.minute ? parseChineseNumberish(groups.minute) : 0;

  if (hour === undefined || minute === undefined || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return undefined;
  }

  const timezoneOffsetMinutes = parseTimezoneOffsetMinutes(nowIso) ?? -baseTime.getTimezoneOffset();
  const local = toLocalDateParts(baseTime, timezoneOffsetMinutes);
  const dayOffset = getDayOffset(groups.day);
  const adjustedHour = adjustHourByPeriod(hour, groups.period);
  const utcMs = Date.UTC(local.year, local.month - 1, local.day + dayOffset, adjustedHour, minute) - timezoneOffsetMinutes * 60_000;

  if (utcMs <= baseTime.getTime()) {
    return undefined;
  }

  return new Date(utcMs).toISOString();
}

function getDayOffset(dayText: string | undefined): number {
  if (dayText === "后天") {
    return 2;
  }

  if (dayText === "大后天") {
    return 3;
  }

  if (dayText === "明天") {
    return 1;
  }

  return 0;
}

function adjustHourByPeriod(hour: number, period: string | undefined): number {
  if ((period === "下午" || period === "晚上" || period === "今晚") && hour < 12) {
    return hour + 12;
  }

  if (period === "中午" && hour < 11) {
    return hour + 12;
  }

  return hour;
}

function toLocalDateParts(date: Date, offsetMinutes: number): { year: number; month: number; day: number } {
  const shifted = new Date(date.getTime() + offsetMinutes * 60_000);

  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate()
  };
}

function parseTimezoneOffsetMinutes(value: string | undefined): number | undefined {
  const match = value?.match(/([+-])(\d{2}):?(\d{2})$/);

  if (!match) {
    return undefined;
  }

  const sign = match[1] === "-" ? -1 : 1;
  return sign * (Number(match[2]) * 60 + Number(match[3]));
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
