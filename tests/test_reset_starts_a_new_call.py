"""กด "คุยใหม่" แล้วต้องได้สายใหม่จริง — ไม่ใช่สายเดิมที่ลบแค่ transcript

บั๊กที่ไฟล์นี้กัน: `reset_pointer` เคยล้างแค่ transcript/turn_count/backend แต่ตำแหน่ง
ของ flow อยู่ในแอตทริบิวต์อีกชุดที่ถูกทิ้งไว้ พอ `_recorded_result` ยังเป็น "ptp" จาก
สายก่อน reply แรกของสายใหม่จะโดน closing gate ตีกลับว่า "บันทึกผลแล้ว เหลือแค่พูด
ประโยคปิดสาย" แล้วโมเดลก็วางสายตั้งแต่เทิร์นยืนยันตัวตน (วัดบน AEON ตอบ "ครับ":
3/3 สายจบตรงนั้นก่อนแก้ · 0/3 หลังแก้)

อีกครึ่งคือ `arguments` ของ tool call ที่ไม่ใช่ object — chat template วน
`tool_call.arguments|items` และ message ถูกเก็บกลับเข้า history ⇒ hop ถัดไป**ทั้งเทิร์น**
ตายด้วย 400 ไม่ใช่แค่ hop ที่พ่นออกมาผิด
"""
import json

from demo_v2.server.sessions import FlowLiveSession, _recover_toolcalls

SPEC = {"states": [{"id": "greet", "initial": True}, {"id": "close", "terminal": True}]}


def _stub():
    """สายที่คุยจบไปแล้วหนึ่งรอบ: ผลถูกสแตมป์ นับ nudge ไปแล้ว และ CRM row โดน
    `_merge_context` เขียนทับด้วยคำตอบของ tool ระหว่างสาย (in place โดยตั้งใจ)"""
    class S:
        pass
    s = S()
    s._spec = SPEC
    s._crm_snapshot = {"total_amount_due": 45000, "due_status": "upcoming"}
    s.customer_data = {"total_amount_due": 45000, "due_status": "upcoming",
                       "record_outcome_id": "REC-1", "result": "ptp"}
    s._recorded_result = "ptp"
    s._cur_states = {"close"}
    s._step_nudges = 2
    s._off_catalog_replies = 3
    s._turn_count = 7
    s.done = True
    s._transcript = [{"user": "ครับ", "hops": []}]
    s._init_agent = lambda: None        # บัง method จริง: ไม่ต้องต่อ backend/API
    return s


def test_reset_clears_the_stamped_result():
    """ตัวที่ทำให้บั๊กโผล่ — ถ้าไม่ล้าง สายใหม่จะถูกบังคับให้พูดประโยคปิดทันที"""
    s = _stub()
    FlowLiveSession.reset_pointer(s)
    assert s._recorded_result is None


def test_reset_clears_every_per_conversation_attribute():
    s = _stub()
    FlowLiveSession.reset_pointer(s)
    assert s._cur_states == {"greet"}     # กลับไปสถานะเริ่มต้นของสเปค
    assert s._step_nudges == 0
    assert s._off_catalog_replies == 0
    assert s._turn_count == 0
    assert s.done is False
    assert s._transcript == []


def test_reset_restores_the_row_the_tenant_gave():
    """เก็บแถว CRM ไว้ไม่ให้ยิง API ซ้ำนั้นถูกแล้ว แต่ต้องเป็นแถวตอนรับมา
    ไม่ใช่แถวที่สายก่อนเขียนทับไว้"""
    s = _stub()
    FlowLiveSession.reset_pointer(s)
    assert s.customer_data == {"total_amount_due": 45000, "due_status": "upcoming"}
    assert "record_outcome_id" not in s.customer_data


def test_recovered_toolcall_arguments_are_always_an_object():
    """arguments ที่เป็น list ทำให้ hop ถัดไปพัง 400 ทั้งเทิร์น — ต้องถูกปัดเป็น {}"""
    leaked = '<tool_call>{"name": "reply", "arguments": [1908]}</tool_call>'
    (call,) = _recover_toolcalls(leaked)
    assert json.loads(call["function"]["arguments"]) == {}


def test_recovered_toolcall_keeps_valid_arguments():
    """แต่ของที่ถูกต้องห้ามโดนทิ้ง — string ที่เป็น JSON object คือรูปแบบปกติของ OpenAI"""
    leaked = '<tool_call>{"name": "reply", "arguments": {"text_ids": [1908]}}</tool_call>'
    (call,) = _recover_toolcalls(leaked)
    assert json.loads(call["function"]["arguments"]) == {"text_ids": [1908]}
