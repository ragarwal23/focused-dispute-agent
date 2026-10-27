// The process panel: what the assistant did for one reply, step by step, each badged AI (a model call),
// code (plain rules) or you. Reps can ignore it; reviewers open it from the "View process" pill.
import { type Owner } from "./api";
import { type AssistantTurn } from "./App";
import { phaseOf } from "./Thread";

const OWNER: Record<Owner, string> = { ai: "AI", code: "code", you: "you" };
const PHASES = ["Reading the complaint", "Investigating the account and the policy", "Drafting the case", "Checking it against policy"];

export default function Process({ turn, onClose }: { turn: AssistantTurn; onClose: () => void }) {
  const phase = phaseOf(turn);
  const steps = turn.steps.filter((s) => s.status !== "done" || s.label);
  return (
    <aside className="process">
      <header className="process-head"><span>How the assistant worked</span><button className="close" onClick={onClose} aria-label="Close">×</button></header>
      <ol className="phase-list">
        {PHASES.map((p, i) => (
          <li key={p} className={i < phase ? "done" : i === phase && turn.streaming ? "active" : ""}>{i < phase ? "●" : i === phase && turn.streaming ? "◉" : "○"} {p}</li>
        ))}
      </ol>
      <div className="process-body">
        <span className="eyebrow">Steps</span>
        {steps.length === 0 && <p className="muted small">{turn.streaming ? "Starting…" : "No steps recorded for this reply (reopened from a saved run)."}</p>}
        <ol className="trail">
          {steps.map((s) => (
            <li key={s.id} className={`step ${s.status} ${s.parent ? "sub" : ""}`}>
              <span className={`badge ${s.owner}`}>{OWNER[s.owner]}</span>
              <span className="step-text">{s.label}{s.status === "running" && <span className="dots" />}{s.status === "stopped" && <em className="muted"> (stopped)</em>}
                {s.detail && <span className="step-detail">{s.detail}</span>}</span>
            </li>
          ))}
        </ol>
      </div>
      <p className="process-foot"><b className="badge ai">AI</b> a model call · <b className="badge code">code</b> plain rules, no model · <b className="badge you">you</b> the rep</p>
    </aside>
  );
}
