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
import { fetchFlowSpec, fetchCueLibrary, saveFlowSpec, type FlowSpecData } from "../api";
import { useMountTransition } from "../hooks/useMountTransition";

const MODAL_EXIT_MS = 380;
const PHASES = ["opening", "main", "close"];
const PHASE_X: Record<string, number> = { opening: 40, main: 340, close: 640 };
// Backend behaviours a tool can bind to (mirrors flowspec.KNOWN_IMPLS).
const KNOWN_IMPLS = [
  "check_account_status", "record_verbal_commitment", "payment_date",
  "callback_datetime", "get_current_datetime", "record_outcome", "update_phone",
  "transfer_to_human_agent", "verify_identity", "generic",
];

type ToolDecl = { name: string; impl: string; orig: any };
type NodeData = {
  name: string;
  phase: string;
  initial?: boolean;
  terminal?: boolean;
  beats: string[];
  groups: number[]; // parallel to beats: same group id = AND (chain), different = OR
  entryTools: string[];
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
  const [cues, setCues] = useState<Record<string, string[]>>({});
  const [library, setLibrary] = useState<Record<string, string[]>>({});
  const [avail, setAvail] = useState<string[]>([]);
  const [beatText, setBeatText] = useState<Record<string, string[]>>({});
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
  // Tool declarations (the set of tools + impl the flow exposes).
  const [tools, setTools] = useState<ToolDecl[]>([]);
  const [toolMode, setToolMode] = useState(false);
  const [tName, setTName] = useState("");
  const [tImpl, setTImpl] = useState(KNOWN_IMPLS[0]);
  const idc = useRef(0);
  const nid = () => `n${idc.current++}`;

  useEffect(() => {
    if (!mounted) return;
    void fetchCueLibrary().then(setLibrary).catch(() => setLibrary({}));
  }, [mounted]);

  useEffect(() => {
    if (!mounted || !company) return;
    setData(null); setNodes([]); setEdges([]); setErrors([]);
    setNewTemplates({}); setSelNode(null); setSelEdge(null);
    void fetchFlowSpec(company)
      .then((d) => {
        setData(d);
        setAvail(d.fine_states);
        setBeatText(d.templates ?? {});
        const evs = (d.spec.events ?? {}) as Record<string, { cues?: string[] }>;
        setEvents(Object.keys(evs));
        setCues(Object.fromEntries(Object.entries(evs).map(([k, v]) => [k, v.cues ?? []])));
        setTools((((d.spec as any).tools?.declarations) ?? []).map((t: any) => ({
          name: t.name, impl: t.impl ?? "generic", orig: t,
        })));
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
              beats: (s.templates ?? []).map((t: any) => t.fine_state),
              groups: (s.templates ?? []).map((t: any, k: number) => (typeof t.group === "number" ? t.group : k)),
              entryTools: s.entry_tools ?? [], orig: s,
            },
          };
        });
        const es: Edge[] = [];
        (d.spec.states ?? []).forEach((s: any) =>
          (s.on ?? []).forEach((t: any, i: number) => {
            if (map[s.id] && map[t.to])
              es.push({
                id: `${map[s.id]}-${i}`, source: map[s.id], target: map[t.to],
                label: t.event, data: { event: t.event, tools: t.tools ?? [], orig: t },
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
      data: { name: `state${i}`, phase: "main", beats: [], groups: [], entryTools: [], orig: {} },
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
    if (!events.includes(ev)) { setEvents((e) => [...e, ev]); setCues((c) => ({ ...c, [ev]: [] })); }
    setNewEvent(""); setErrors([]);
  };
  const removeEvent = (ev: string) => {
    setEvents((e) => e.filter((x) => x !== ev));
    setCues((c) => { const n = { ...c }; delete n[ev]; return n; });
    setEdges((es) => es.filter((e) => e.data?.event !== ev));
  };
  const addCue = (ev: string, cue: string) => {
    const v = cue.trim();
    if (!v) return;
    setCues((c) => ((c[ev] ?? []).includes(v) ? c : { ...c, [ev]: [...(c[ev] ?? []), v] }));
  };
  const removeCue = (ev: string, cue: string) =>
    setCues((c) => ({ ...c, [ev]: (c[ev] ?? []).filter((x) => x !== cue) }));

  // --- tools ---
  const toolNames = tools.map((t) => t.name);
  const addTool = () => {
    const n = tName.trim();
    if (!/^[a-z][a-z0-9_]*$/.test(n)) { setErrors(["ชื่อ tool ใช้ a-z/0-9/_ ขึ้นต้นด้วยตัวอักษร"]); return; }
    if (toolNames.includes(n)) { setErrors([`มี tool ${n} อยู่แล้ว`]); return; }
    setTools((ts) => [...ts, { name: n, impl: tImpl, orig: { name: n, impl: tImpl } }]);
    setToolMode(false); setTName(""); setErrors([]);
  };
  const removeTool = (n: string) => {
    setTools((ts) => ts.filter((t) => t.name !== n));
    setNodes((ns) => ns.map((x) => ({ ...x, data: { ...x.data, entryTools: x.data.entryTools.filter((t) => t !== n) } })));
    setEdges((es) => es.map((e) => ({ ...e, data: { ...e.data, tools: (e.data?.tools ?? []).filter((t: string) => t !== n) } })));
  };
  const addEntryTool = (id: string, t: string) =>
    patchNode(id, (d) => (d.entryTools.includes(t) ? d : { ...d, entryTools: [...d.entryTools, t] }));
  const removeEntryTool = (id: string, t: string) =>
    patchNode(id, (d) => ({ ...d, entryTools: d.entryTools.filter((x) => x !== t) }));
  const patchEdgeTools = (id: string, fn: (arr: string[]) => string[]) =>
    setEdges((es) => es.map((e) => (e.id === id ? { ...e, data: { ...e.data, tools: fn(e.data?.tools ?? []) } } : e)));

  // beats composer helpers (state declared above, before the early return)
  // group === "new" → put the beat in a brand-new OR group; a number → that group
  const addExistingBeat = (id: string, fs: string, group: number | "new" = "new") =>
    patchNode(id, (d) => {
      if (d.beats.includes(fs)) return d;
      const g = group === "new" ? (d.groups.length ? Math.max(...d.groups) + 1 : 0) : group;
      return { ...d, beats: [...d.beats, fs], groups: [...d.groups, g] };
    });
  const [beatGroup, setBeatGroup] = useState<number | "new">("new"); // group the composer adds into
  const createBeat = (id: string) => {
    const fs = bFs.trim();
    if (!/^[a-z][a-z0-9_]*$/.test(fs)) { setErrors(["fine_state ใช้ a-z/0-9/_"]); return; }
    if (!bText.trim()) { setErrors(["ใส่ข้อความ beat"]); return; }
    if (avail.includes(fs)) { setErrors([`มี ${fs} อยู่แล้ว`]); return; }
    setNewTemplates((m) => ({ ...m, [fs]: bText.trim() }));
    setAvail((a) => [...a, fs]);
    addExistingBeat(id, fs, beatGroup);
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
        const origTmpls: any[] = n.data.orig?.templates ?? [];
        s.templates = n.data.beats.map((fs, k) => {
          const prev = origTmpls.find((t: any) => t.fine_state === fs);
          return { ...(prev ?? { fine_state: fs }), group: n.data.groups[k] ?? 0 };
        });
        delete s.template_mode; // superseded by per-template group
        if (n.data.entryTools.length) s.entry_tools = n.data.entryTools; else delete s.entry_tools;
        s.on = [];
        return s;
      });
      const byRf: Record<string, any> = {};
      spec.states.forEach((s: any, i: number) => (byRf[nodes[i].id] = s));
      edges.forEach((e) => {
        const src = byRf[e.source]; const tgt = nodes.find((n) => n.id === e.target);
        if (src && tgt) {
          const extra = e.data?.orig ? { ...e.data.orig } : {};
          const tr: any = { ...extra, event: e.data?.event ?? e.label, to: tgt.data.name };
          if ((e.data?.tools ?? []).length) tr.tools = e.data.tools; else delete tr.tools;
          src.on.push(tr);
        }
      });
      // events: keep orig desc, write edited cues (what the model matches on)
      const evOut: Record<string, any> = {};
      for (const ev of events) {
        const orig = (data.spec.events ?? {})[ev] ?? {};
        const cueList = cues[ev] ?? [];
        evOut[ev] = { ...orig, ...(cueList.length ? { cues: cueList } : {}) };
        if (!evOut[ev].desc) evOut[ev].desc = "";
      }
      spec.events = evOut;
      // tool declarations (preserve gating/args from orig; add new by impl)
      spec.tools = spec.tools ?? {};
      spec.tools.declarations = tools.map((t) => ({ ...(t.orig || {}), name: t.name, impl: t.impl }));

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
                  <p className="fx-note" style={{ margin: "2px 0 0" }}>
                    กลุ่มเดียวกัน = <b>พูดต่อกัน (AND)</b> · คนละกลุ่ม = <b>เลือกกลุ่มเดียว (OR)</b>
                  </p>
                  {(() => {
                    const d = selectedNode.data;
                    const beats = d.beats, groups = d.groups;
                    const origT: any[] = d.orig?.templates ?? [];
                    const gids: number[] = [];
                    groups.forEach((g) => { if (!gids.includes(g)) gids.push(g); });
                    const rm = (i: number) => patchNode(selectedNode.id, (x) => ({
                      ...x, beats: x.beats.filter((_, j) => j !== i), groups: x.groups.filter((_, j) => j !== i),
                    }));
                    const swap = (i: number, k: number) => patchNode(selectedNode.id, (x) => {
                      const b = [...x.beats], g = [...x.groups];
                      [b[i], b[k]] = [b[k], b[i]]; return { ...x, beats: b, groups: g };
                    });
                    const avOpts = avail.filter((fs) => !beats.includes(fs));
                    return (
                      <div className="fx-groups">
                        {gids.map((gid, gi) => {
                          const idxs = beats.map((_, i) => i).filter((i) => groups[i] === gid);
                          return (
                            <div key={gid}>
                              {gi > 0 && <div className="fx-grp-or">— หรือ —</div>}
                              <div className="fx-grp-box">
                                {idxs.map((i, pos) => {
                                  const fs = beats[i];
                                  const lines = newTemplates[fs] != null ? [newTemplates[fs]] : (beatText[fs] ?? []);
                                  const whenEv = origT.find((t) => t.fine_state === fs)?.when_event;
                                  return (
                                    <div className="fx-bf-item" key={i}>
                                      {pos > 0 && <div className="fx-bf-conn and">↓ แล้วพูดต่อ</div>}
                                      <div className="fx-bf-node">
                                        <div className="fx-bf-head">
                                          <code>{fs}</code>
                                          {fs in newTemplates && <span className="fx-new">ใหม่</span>}
                                          {whenEv && <span className="fx-bf-when">เมื่อ {whenEv}</span>}
                                          <span className="fx-bf-actions">
                                            {pos > 0 && <button title="ขึ้น" onClick={() => swap(i, idxs[pos - 1])}>▲</button>}
                                            {pos < idxs.length - 1 && <button title="ลง" onClick={() => swap(i, idxs[pos + 1])}>▼</button>}
                                            <button className="fx-rm" onClick={() => rm(i)}><X size={11} /></button>
                                          </span>
                                        </div>
                                        {lines.length ? lines.map((t, j) => <p className="fx-bf-line" key={j}>“{t}”</p>)
                                          : <p className="fx-bf-line fx-script-empty">(ยังไม่มีข้อความ)</p>}
                                      </div>
                                    </div>
                                  );
                                })}
                                <select className="fx-add-select fx-grp-add" value=""
                                  onChange={(e) => { if (e.target.value) addExistingBeat(selectedNode.id, e.target.value, gid); }}>
                                  <option value="">＋ ต่อบทในกลุ่มนี้ (AND)…</option>
                                  {avOpts.map((fs) => <option key={fs} value={fs}>{fs}</option>)}
                                </select>
                              </div>
                            </div>
                          );
                        })}
                        {beats.length > 0 && <div className="fx-grp-or">— หรือ —</div>}
                        <select className="fx-add-select" value=""
                          onChange={(e) => { if (e.target.value === "__new__") { setBeatGroup("new"); setBeatMode(true); } else if (e.target.value) addExistingBeat(selectedNode.id, e.target.value, "new"); }}>
                          <option value="">＋ กลุ่มใหม่ (OR)…</option>
                          <option value="__new__">＋ สร้างบทใหม่ (กลุ่มใหม่)…</option>
                          {avOpts.map((fs) => <option key={fs} value={fs}>{fs}</option>)}
                        </select>
                      </div>
                    );
                  })()}
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
                <div className="fx-field">
                  <label>เรียก tool ตอนเข้า state (silent)</label>
                  <div className="fx-chips">
                    {selectedNode.data.entryTools.map((t, i) => (
                      <span className="fx-chip fx-event" key={i}>{t}
                        <button className="fx-rm" onClick={() => removeEntryTool(selectedNode.id, t)}><X size={11} /></button>
                      </span>
                    ))}
                    <select className="fx-add-select" value=""
                      onChange={(e) => { if (e.target.value) addEntryTool(selectedNode.id, e.target.value); }}>
                      <option value="">＋ tool…</option>
                      {toolNames.filter((t) => !selectedNode.data.entryTools.includes(t)).map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </div>
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
                <div className="fx-field">
                  <label>เรียก tool ตอน transition นี้</label>
                  <div className="fx-chips">
                    {(selectedEdge.data?.tools ?? []).map((t: string, i: number) => (
                      <span className="fx-chip fx-event" key={i}>{t}
                        <button className="fx-rm" onClick={() => patchEdgeTools(selectedEdge.id, (a) => a.filter((x) => x !== t))}><X size={11} /></button>
                      </span>
                    ))}
                    <select className="fx-add-select" value=""
                      onChange={(e) => { const v = e.target.value; if (v) patchEdgeTools(selectedEdge.id, (a) => a.includes(v) ? a : [...a, v]); }}>
                      <option value="">＋ tool…</option>
                      {toolNames.filter((t) => !(selectedEdge.data?.tools ?? []).includes(t)).map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </div>
                </div>
                <button className="fx-btn fx-mini fx-danger" onClick={() => deleteEdge(selectedEdge.id)}><Trash2 size={12} /> ลบทางแยก</button>
              </>
            ) : (
              <>
                <h3>Events + cues</h3>
                <p className="fx-note" style={{ margin: 0 }}>
                  cues = ตัวอย่างคำที่ลูกค้าพูดเพื่อ <b>trigger transition</b> นี้ — <b>ไม่มี cues → transition มักไม่ยิง</b> (โมเดลไม่รู้ว่าจะเปลี่ยน state ตอนไหน)
                </p>
                <div className="fx-events-list">
                  {events.map((ev) => (
                    <div className="fx-event-row" key={ev}>
                      <div className="fx-event-name">
                        <code>{ev}</code>
                        {(cues[ev] ?? []).length === 0 && <span className="fx-warn-tag">ไม่มี cues</span>}
                        <button className="fx-rm" onClick={() => removeEvent(ev)} aria-label={`remove ${ev}`}><X size={11} /></button>
                      </div>
                      <div className="fx-chips">
                        {(cues[ev] ?? []).map((cue, i) => (
                          <span className="fx-chip" key={i}>{cue}
                            <button className="fx-rm" onClick={() => removeCue(ev, cue)}><X size={10} /></button>
                          </span>
                        ))}
                        <input
                          className="fx-cue-input"
                          placeholder="+ คำ (Enter)"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") { addCue(ev, e.currentTarget.value); e.currentTarget.value = ""; }
                          }}
                        />
                      </div>
                      {(() => {
                        const sugg = (library[ev] ?? []).filter((c) => !(cues[ev] ?? []).includes(c));
                        if (sugg.length === 0) return null;
                        return (
                          <div className="fx-suggest">
                            <span className="fx-suggest-lbl">แนะนำ</span>
                            {sugg.map((c) => (
                              <button className="fx-sugg-chip" key={c} onClick={() => addCue(ev, c)} title="คลิกเพื่อเพิ่ม">
                                <Plus size={9} /> {c}
                              </button>
                            ))}
                            <button className="fx-sugg-all" onClick={() => sugg.forEach((c) => addCue(ev, c))}>+ ทั้งหมด</button>
                          </div>
                        );
                      })()}
                    </div>
                  ))}
                </div>
                <div className="fx-composer-row" style={{ marginTop: 8 }}>
                  <input className="fx-ev-input" placeholder="event ใหม่" value={newEvent} onChange={(e) => setNewEvent(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") addEvent(); }} />
                  <button className="fx-btn fx-mini" onClick={addEvent}><Plus size={12} /> event</button>
                </div>
                <div className="fx-divider" />
                <h3>Tools</h3>
                <div className="fx-chips">
                  {tools.map((t) => (
                    <span className="fx-chip fx-event" key={t.name} title={`impl: ${t.impl}`}>{t.name}
                      <button className="fx-rm" onClick={() => removeTool(t.name)}><X size={11} /></button>
                    </span>
                  ))}
                </div>
                {toolMode ? (
                  <div className="fx-composer" style={{ marginTop: 6 }}>
                    <input placeholder="ชื่อ tool (เช่น send_sms)" value={tName} onChange={(e) => setTName(e.target.value)} />
                    <select value={tImpl} onChange={(e) => setTImpl(e.target.value)}>
                      {KNOWN_IMPLS.map((im) => <option key={im} value={im}>{im}</option>)}
                    </select>
                    <div className="fx-composer-row">
                      <button className="fx-btn fx-mini" onClick={addTool}>เพิ่ม</button>
                      <button className="fx-btn fx-mini fx-ghost" onClick={() => setToolMode(false)}>ยกเลิก</button>
                    </div>
                  </div>
                ) : (
                  <button className="fx-btn fx-mini fx-addbeat" style={{ marginTop: 4 }} onClick={() => setToolMode(true)}><Plus size={12} /> เพิ่ม tool</button>
                )}
                <div className="fx-divider" />
                <p className="fx-note" style={{ margin: 0 }}>
                  🖱️ <b>ลากกล่อง</b> = ย้าย · ลากจากจุดขวากล่อง → อีกกล่อง = <b>สร้างทางแยก</b> · <b>คลิกกล่อง/เส้น</b> = แก้ตรงนี้ · <b>tool</b> ผูกที่ state (entry) หรือ transition
                </p>
              </>
            )}
          </div>
        </div>

        <div className="fx-foot">
          <span className="fx-foot-hint">บันทึกแล้ว validate อัตโนมัติ · แก้ tools/entry/transition ได้ (constraints/gating ขั้นสูงเก็บให้) · ลบ state/tool แล้ว ref ที่ค้างถูกตัดให้เอง</span>
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
