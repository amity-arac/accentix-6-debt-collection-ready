export const TOOL_DESCRIPTIONS: Record<string, string> = {
  verify_identity: "Verify customer identity",
  check_account_status: "Fetch account state",
  get_current_datetime: "Get current date/time",
  record_verbal_commitment: "Record verbal commitment (no CRM write yet)",
  payment_date: "Write payment commitment to CRM",
  callback_datetime: "Schedule callback",
};

export function describeTool(name: string): string {
  return TOOL_DESCRIPTIONS[name] ?? name;
}

export type ToolCategory = "KYC" | "READ" | "WRITE";

export const TOOL_CATEGORY: Record<string, ToolCategory> = {
  verify_identity: "KYC",
  check_account_status: "READ",
  get_current_datetime: "READ",
  record_verbal_commitment: "WRITE",
  payment_date: "WRITE",
  callback_datetime: "WRITE",
};

export function toolCategory(name: string): ToolCategory | null {
  return TOOL_CATEGORY[name] ?? null;
}

const RESULT_REASON_LABEL: Record<string, string> = {
  account_under_review: "Account under review",
  account_closed: "Account closed",
  date_format_invalid: "Invalid date format",
  verbal_commitment_missing_or_mismatch: "Missing or mismatched commitment",
  channel_required: "Payment channel required",
  channel_invalid: "Invalid payment channel",
  incompatible_chain: "Incompatible templates",
  category_lock: "Category lock",
  state_lock: "State mismatch",
  dispute_lock: "Disputed account — no payment recording",
};

export const RESULT_KEY_LABEL: Record<string, string> = {
  verified: "Verified",
  recorded: "Recorded",
  reason: "Reason",
  id: "ID",
  customer_name: "Customer",
  last_4_digits: "Last 4",
  total_amount_due: "Balance",
  minimum_payment_due: "Minimum",
  due_date: "Due date",
  due_status: "Due status",
  case_status: "Case status",
  case_status_note: "Note",
  loan_type: "Loan",
  amount: "Amount",
  date: "Date",
  channel: "Channel",
  today: "Today",
  tomorrow: "Tomorrow",
  day_after_tomorrow: "Day after tomorrow",
  in_one_week: "In one week",
  expected: "Expected",
  expected_weekday: "Expected weekday",
  got: "Got",
  hint: "Hint",
  missing: "Missing",
  next_action: "Next action",
};

/** Returns a friendly English summary for a backend tool result, or null. */
export function friendlyResultSummary(r: unknown): string | null {
  if (!r || typeof r !== "object") return null;
  const obj = r as Record<string, unknown>;
  if (obj.verified === true) return "Verified";
  if (obj.recorded === true) return "Recorded";
  if (obj.recorded === false && typeof obj.reason === "string") {
    const label = RESULT_REASON_LABEL[obj.reason];
    return label ? `Not recorded: ${label}` : `Not recorded: ${obj.reason}`;
  }
  if (typeof obj.reason === "string" && RESULT_REASON_LABEL[obj.reason]) {
    return RESULT_REASON_LABEL[obj.reason];
  }
  return null;
}
