import { Headphones, Wrench, FileText } from "lucide-react";
import { COMPANY_LABELS } from "../api";

type Props = {
  company: string;
  onPlay: () => void;
  onEdit: () => void;
  onView: () => void;
  onBack: () => void;
};

export function ModeSelect({ company, onPlay, onEdit, onView, onBack }: Props) {
  const label = COMPANY_LABELS[company] ?? company;
  return (
    <div className="studio">
      <div className="studio-crumb">
        <button onClick={onBack}>บริษัท</button>
        <span className="sep">›</span>
        <b>
          {label} ({company})
        </b>
      </div>
      <p className="studio-step">จะทำอะไรกับ {label}</p>
      <div className="mode-grid">
        <button className="mode-card" onClick={onPlay}>
          <span className="mode-ic">
            <Headphones size={26} aria-hidden="true" />
          </span>
          <span className="mode-t">Playground</span>
          <span className="mode-d">
            คุยกับบอทเหมือนโทรจริง — เลือกลูกค้า, เอเจนต์, เสียง แล้วทดสอบ flow
          </span>
          <span className="mode-go">เข้า playground →</span>
        </button>
        <button className="mode-card" onClick={onEdit}>
          <span className="mode-ic">
            <Wrench size={24} aria-hidden="true" />
          </span>
          <span className="mode-t">Edit flow</span>
          <span className="mode-d">
            แก้โครง flow แบบลากวาง — states, transitions, beats, events, tools
          </span>
          <span className="mode-go">เปิด editor →</span>
        </button>
        <button className="mode-card" onClick={onView}>
          <span className="mode-ic">
            <FileText size={24} aria-hidden="true" />
          </span>
          <span className="mode-t">อ่าน instruction</span>
          <span className="mode-d">
            ดู prompt ที่โมเดลอ่านจริง — render สดจาก flow ปัจจุบัน
          </span>
          <span className="mode-go">เปิดดู →</span>
        </button>
      </div>
    </div>
  );
}
