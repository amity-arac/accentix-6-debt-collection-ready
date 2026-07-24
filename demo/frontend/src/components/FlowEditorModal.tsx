import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
  type Node,
  type Edge,
  type NodeProps,
  type Connection,
} from "reactflow";
import "reactflow/dist/style.css";
import { X, Plus, Trash2 } from "lucide-react";
import { fetchFlowSpec, saveFlowSpec, type FlowSpecData } from "../api";
import { useMountTransition } from "../hooks/useMountTransition";

const MODAL_EXIT_MS = 380;
const PHASES = ["opening", "main", "close"];
const PHASE_X: Record<string, number> = { opening: 40, main: 340, close: 640 };

type NodeData = {
  name: string;
  phase: string;
  initial?: boolean;
  terminal?: boolean;
  beats: string[];
  orig: any;
};

// ---- custom state node ----
function FlowStateNode({ data, selected }: NodeProps<NodeData>) {
  return (
    <div className={`rf-node${selected ? " sel" : ""}${data.initial ? " start" : ""}${data.terminal ? " end" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="rf-node-nm">
        {data.name}
        {data.initial && <span className="rf-badge s">▶</span>}
        {data.terminal && <span className="rf-badge e">■</span>}
      </div>
      <div className="rf-node-ph">{data.phase}</div>
      {data.beats.length > 0 && (
        <div className="rf-node-beats">
          {data.beats.slice(0, 4).map((b, i) => (
            <span key={i} className="rf-node-beat">{b}</span>
          ))}
          {data.beats.length > 4 && <span className="rf-node-beat">+{data.beats.length - 4}</span>}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const NODE_TYPES = { flowState: FlowStateNode };

type Props = {
  open: boolean;
  company: string | null;
  onClose: () => void;
  onSaved: (company: string) => void;
};

export function FlowEditorModal({ open, company, onClose, onSaved }: Props) {
  const { mounted, visible } = useMountTransition(open, MODAL_EXIT_MS);
  const [data, setData] = useState<FlowSpecData | null>(null);
  const [nodes, setNodes] = useState<Node<NodeData>[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [avail, setAvail] = useState<string[]>([]);
  const [newTemplates, setNewTemplates] = useState<Record<string, string>>({});
  const [selNode, setSelNode] = useState<string | null>(null);
  const [selEdge, setSelEdge] = useState<string | null>(null);
  const [newEvent, setNewEvent] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  // Beat composer (per selected node). MUST be declared before any early return
  // so the hook order stays stable.
  const [beatMode, setBeatMode] = useState(false);
  const [bFs, setBFs] = useState("");
  const [bText, setBText] = useState("");
  const idc = useRef(0);
  const nid = () => `n${idc.current++}`;

  useEffect(() => {
    if (!mounted || !company) return;
    setData(null); setNodes([]); setEdges([]); setErrors([]);
    setNewTemplates({}); setSelNode(null); setSelEdge(null);
    void fetchFlowSpec(company)
      .then((d) => {
        setData(d);
        setAvail(d.fine_states);
        setEvents(Object.keys(d.spec.events ?? {}));
        const byPhaseCount: Record<string, number> = {};
        const map: Record<string, string> = {};
        const ns: Node<NodeData>[] = (d.spec.states ?? []).map((s: any) => {
          const rid = nid();
          map[s.id] = rid;
          const phase = s.phase ?? "main";
          const y = 30 + (byPhaseCount[phase] = (byPhaseCount[phase] ?? 0) + 1, (byPhaseCount[phase] - 1) * 118);
          return {
            id: rid, type: "flowState", position: { x: PHASE_X[phase] ?? 340, y },
            data: {
              name: s.id, phase, initial: !!s.initial, terminal: !!s.terminal,
              beats: (s.templates ?? []).map((t: any) => t.fine_state), orig: s,
            },
          };
        });
        const es: Edge[] = [];
        (d.spec.states ?? []).forEach((s: any) =>
          (s.on ?? []).forEach((t: any, i: number) => {
            if (map[s.id] && map[t.to])
              es.push({
                id: `${map[s.id]}-${i}`, source: map[s.id], target: map[t.to],
                label: t.event, data: { event: t.event, orig: t },
                markerEnd: { type: MarkerType.ArrowClosed },
              });
          }),
        );
        setNodes(ns); setEdges(es);
      })
      .catch(() => setErrors([`โหลด spec ของ ${company} ไม่สำเร็จ`]));
  }, [mounted, company]);

  const onNodesChange = useCallback((c: any) => setNodes((ns) => applyNodeChanges(c, ns)), []);
  const onEdgesChange = useCallback((c: any) => setEdges((es) => applyEdgeChanges(c, es)), []);
  const onConnect = useCallback(
    (conn: Connection) => {
      if (!events.length) { setErrors(["สร้าง event ก่อน (ทางแยกต้องมี event)"]); return; }
      setEdges((es) =>
        addEdge(
          { ...conn, label: events[0], data: { event: events[0] }, markerEnd: { type: MarkerType.ArrowClosed } },
          es,
        ),
      );
    },
    [events],
  );

  const patchNode = (id: string, fn: (d: NodeData) => NodeData) =>
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: fn(n.data) } : n)));
  const patchEdge = (id: string, ev: string) =>
    setEdges((es) => es.map((e) => (e.id === id ? { ...e, label: ev, data: { ...e.data, event: ev } } : e)));

  const selectedNode = useMemo(() => nodes.find((n) => n.id === selNode) ?? null, [nodes, selNode]);
  const selectedEdge = useMemo(() => edges.find((e) => e.id === selEdge) ?? null, [edges, selEdge]);
  const nodeName = (id: string) => nodes.find((n) => n.id === id)?.data.name ?? id;

  if (!mounted) return null;

  const addState = () => {
    let i = 1; while (nodes.some((n) => n.data.name === `state${i}`)) i++;
    const id = nid();
    setNodes((ns) => [...ns, {
      id, type: "flowState", position: { x: 340, y: 40 + ns.length * 20 },
      data: { name: `state${i}`, phase: "main", beats: [], orig: {} },
    }]);
    setSelNode(id); setSelEdge(null);
  };
  const deleteNode = (id: string) => {
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setSelNode(null);
  };
  const deleteEdge = (id: string) => { setEdges((es) => es.filter((e) => e.id !== id)); setSelEdge(null); };
  const addEvent = () => {
    const ev = newEvent.trim();
    if (!/^[a-z][a-z0-9_]*$/.test(ev)) { setErrors(["event ใช้ a-z/0-9/_ ขึ้นต้นด้วยตัวอักษร"]); return; }
    if (!events.includes(ev)) setEvents((e) => [...e, ev]);
    setNewEvent(""); setErrors([]);
  };
  const removeEvent = (ev: string) => {
    setEvents((e) => e.filter((x) => x !== ev));
    setEdges((es) => es.filter((e) => e.data?.event !== ev));
  };

  // beats composer helpers (state declared above, before the early return)
  const addExistingBeat = (id: string, fs: string) =>
    patchNode(id, (d) => (d.beats.includes(fs) ? d : { ...d, beats: [...d.beats, fs] }));
  const createBeat = (id: string) => {
    const fs = bFs.trim();
    if (!/^[a-z][a-z0-9_]*$/.test(fs)) { setErrors(["fine_state ใช้ a-z/0-9/_"]); return; }
    if (!bText.trim()) { setErrors(["ใส่ข้อความ beat"]); return; }
    if (avail.includes(fs)) { setErrors([`มี ${fs} อยู่แล้ว`]); return; }
    setNewTemplates((m) => ({ ...m, [fs]: bText.trim() }));
    setAvail((a) => [...a, fs]);
    addExistingBeat(id, fs);
    setBeatMode(false); setBFs(""); setBText(""); setErrors([]);
  };

  const save = async () => {
    if (!data || !company) return;
    setErrors([]); setSaving(true);
    try {
      const spec: any = JSON.parse(JSON.stringify(data.spec));
      // states from nodes
      spec.states = nodes.map((n) => {
        const s: any = { ...(n.data.orig || {}) };
        s.id = n.data.name;
        s.phase = n.data.phase;
        if (n.data.initial) s.initial = true; else delete s.initial;
        if (n.data.terminal) s.terminal = true; else delete s.terminal;
        s.templates = n.data.beats.map((fs) => ({ fine_state: fs }));
        s.on = [];
        return s;
      });
      const byRf: Record<string, any> = {};
      spec.states.forEach((s: any, i: number) => (byRf[nodes[i].id] = s));
      edges.forEach((e) => {
        const src = byRf[e.source]; const tgt = nodes.find((n) => n.id === e.target);
        if (src && tgt) {
          const extra = e.data?.orig ? { ...e.data.orig } : {};
          src.on.push({ ...extra, event: e.data?.event ?? e.label, to: tgt.data.name });
        }
      });
      // events (keep descriptions where they existed)
      const evOut: Record<string, any> = {};
      for (const ev of events) evOut[ev] = (data.spec.events ?? {})[ev] ?? { desc: "" };
      spec.events = evOut;

      const res = await saveFlowSpec(
        company, spec,
        Object.entries(newTemplates).map(([fine_state, template]) => ({ fine_state, template })),
      );
      if (res.ok) onSaved(company);
      else setErrors(res.errors ?? ["บันทึกไม่สำเร็จ"]);
    } catch (e: any) {
      setErrors([`บันทึกไม่สำเร็จ: ${e?.message ?? e}`]);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={`persona-modal-backdrop${visible ? " open" : ""}`} onClick={onClose} role="presentation">
      <div
        className="fx-modal fx-graph"
        role="dialog" aria-modal="true" tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); e.stopPropagation(); }}
      >
        <div className="fx-head">
          <h2>แก้ Flow — <span className="fx-accent">{company}</span></h2>
          <span className="fx-step">drag &amp; drop</span>
          <button className="fx-btn fx-mini" onClick={addState}><Plus size={12} /> เพิ่ม state</button>
          <button className="fx-x" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>

        {errors.length > 0 && (
          <div className="fx-errors" role="alert" style={{ margin: "8px 16px 0" }}>
            {errors.map((e, i) => <div key={i}>{e}</div>)}
          </div>
        )}

        <div className="fx-graph-body">
          <div className="fx-canvas">
            {data && (
              <ReactFlow
                nodes={nodes} edges={edges}
                nodeTypes={NODE_TYPES}
                onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
                onNodeClick={(_, n) => { setSelNode(n.id); setSelEdge(null); }}
                onEdgeClick={(_, e) => { setSelEdge(e.id); setSelNode(null); }}
                onPaneClick={() => { setSelNode(null); setSelEdge(null); }}
                fitView proOptions={{ hideAttribution: true }}
              >
                <Background gap={22} />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
          </div>

          <div className="fx-side">
            {selectedNode ? (
              <>
                <h3>แก้ state</h3>
                <div className="fx-field">
                  <label>ชื่อ state (แก้ได้)</label>
                  <input value={selectedNode.data.name}
                    onChange={(e) => patchNode(selectedNode.id, (d) => ({ ...d, name: e.target.value }))} />
                </div>
                <div className="fx-field">
                  <label>ช่วง (phase)</label>
                  <select value={selectedNode.data.phase}
                    onChange={(e) => patchNode(selectedNode.id, (d) => ({ ...d, phase: e.target.value }))}>
                    {PHASES.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </div>
                <div className="fx-side-checks">
                  <label><input type="checkbox" checked={!!selectedNode.data.initial}
                    onChange={(e) => patchNode(selectedNode.id, (d) => ({ ...d, initial: e.target.checked }))} /> เริ่มต้น</label>
                  <label><input type="checkbox" checked={!!selectedNode.data.terminal}
                    onChange={(e) => patchNode(selectedNode.id, (d) => ({ ...d, terminal: e.target.checked }))} /> จบสาย</label>
                </div>
                <div className="fx-field">
                  <label>พูด (beats)</label>
                  <div className="fx-chips">
                    {selectedNode.data.beats.map((fs, i) => (
                      <span className="fx-chip" key={i}>{fs}
                        {fs in newTemplates && <span className="fx-new">ใหม่</span>}
                        <button className="fx-rm" onClick={() => patchNode(selectedNode.id, (d) => ({ ...d, beats: d.beats.filter((_, j) => j !== i) }))}><X size={11} /></button>
                      </span>
                    ))}
                    <select className="fx-add-select" value=""
                      onChange={(e) => { if (e.target.value === "__new__") setBeatMode(true); else if (e.target.value) addExistingBeat(selectedNode.id, e.target.value); }}>
                      <option value="">＋ เพิ่มบท…</option>
                      <option value="__new__">＋ สร้างบทใหม่…</option>
                      {avail.map((fs) => <option key={fs} value={fs}>{fs}</option>)}
                    </select>
                  </div>
                  {beatMode && (
                    <div className="fx-composer" style={{ marginTop: 6 }}>
                      <input placeholder="ชื่อ beat (เช่น offer_promo)" value={bFs} onChange={(e) => setBFs(e.target.value)} />
                      <textarea placeholder="ข้อความ (ใช้ {customer_name} {amount} {suffix})" rows={2} value={bText} onChange={(e) => setBText(e.target.value)} />
                      <div className="fx-composer-row">
                        <button className="fx-btn fx-mini" onClick={() => createBeat(selectedNode.id)}>เพิ่ม</button>
                        <button className="fx-btn fx-mini fx-ghost" onClick={() => setBeatMode(false)}>ยกเลิก</button>
                      </div>
                    </div>
                  )}
                </div>
                <div className="fx-divider" />
                <button className="fx-btn fx-mini fx-danger" onClick={() => deleteNode(selectedNode.id)}><Trash2 size={12} /> ลบ state นี้</button>
              </>
            ) : selectedEdge ? (
              <>
                <h3>แก้ทางแยก</h3>
                <div className="fx-note" style={{ margin: 0 }}>{nodeName(selectedEdge.source)} → {nodeName(selectedEdge.target)}</div>
                <div className="fx-field">
                  <label>เมื่อเกิด event</label>
                  <select value={selectedEdge.data?.event ?? ""} onChange={(e) => patchEdge(selectedEdge.id, e.target.value)}>
                    {events.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
                  </select>
                </div>
                <button className="fx-btn fx-mini fx-danger" onClick={() => deleteEdge(selectedEdge.id)}><Trash2 size={12} /> ลบทางแยก</button>
              </>
            ) : (
              <>
                <h3>Events</h3>
                <div className="fx-chips">
                  {events.map((ev) => (
                    <span className="fx-chip fx-event" key={ev}>{ev}
                      <button className="fx-rm" onClick={() => removeEvent(ev)}><X size={11} /></button>
                    </span>
                  ))}
                </div>
                <div className="fx-composer-row" style={{ marginTop: 4 }}>
                  <input className="fx-ev-input" placeholder="event ใหม่" value={newEvent} onChange={(e) => setNewEvent(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") addEvent(); }} />
                  <button className="fx-btn fx-mini" onClick={addEvent}><Plus size={12} /></button>
                </div>
                <div className="fx-divider" />
                <p className="fx-note" style={{ margin: 0 }}>
                  🖱️ <b>ลากกล่อง</b> = ย้าย · ลากจากจุดขวากล่อง → อีกกล่อง = <b>สร้างทางแยก</b> · <b>คลิกกล่อง/เส้น</b> = แก้ตรงนี้
                </p>
              </>
            )}
          </div>
        </div>

        <div className="fx-foot">
          <span className="fx-foot-hint">บันทึกแล้ว validate อัตโนมัติ · ระบบเก็บ tools/constraints ให้ · ลบ state แล้วทางแยกที่ชี้หามันลบให้เอง</span>
          <span className="fx-spacer" />
          <button className="fx-btn fx-ghost" onClick={onClose}>ยกเลิก</button>
          <button className="fx-btn fx-primary" onClick={() => void save()} disabled={saving || !data}>
            {saving ? "กำลังบันทึก…" : "บันทึก flow"}
          </button>
        </div>
      </div>
    </div>
  );
}
