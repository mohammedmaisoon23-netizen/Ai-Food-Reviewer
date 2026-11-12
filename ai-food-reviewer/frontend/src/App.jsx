import { useCallback, useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_BASE || "";

const STEPS = [
  { key: "script", label: "Script", upTo: 30 },
  { key: "voice", label: "Voiceover", upTo: 65 },
  { key: "video", label: "Video", upTo: 94 },
  { key: "final", label: "Mix & export", upTo: 100 },
];

async function api(path, options) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

function CharacterAvatar({ color, name }) {
  return (
    <svg viewBox="0 0 64 64" className="h-14 w-14 shrink-0" aria-hidden="true">
      <circle cx="32" cy="32" r="32" fill="rgba(255,255,255,.06)" />
      <path d="M8 64c0-14 10-20 24-20s24 6 24 20z" fill={color} />
      <circle cx="32" cy="29" r="14" fill="#d29a6a" />
      <path d="M17 27c0-10 7-15 15-15s15 5 15 15c-4-4-9-6-15-6s-11 2-15 6z" fill="#1c1614" />
      <circle cx="26.5" cy="30" r="2" fill="#1c1614" />
      <circle cx="37.5" cy="30" r="2" fill="#1c1614" />
      <path d="M26 37q6 5 12 0" stroke="#7a2f2f" strokeWidth="2.2" fill="none" strokeLinecap="round" />
      <title>{name}</title>
    </svg>
  );
}

function Stepper({ progress, failed }) {
  return (
    <ol className="grid grid-cols-4 gap-2 text-xs">
      {STEPS.map((s, i) => {
        const prev = i === 0 ? 0 : STEPS[i - 1].upTo;
        const done = progress >= s.upTo;
        const active = !done && progress >= prev;
        return (
          <li key={s.key} className="min-w-0">
            <div className={`h-1.5 rounded-full ${done ? "bg-saffron" : active ? (failed ? "bg-red-500" : "bg-chili") : "bg-white/10"}`} />
            <p className={`mt-1.5 truncate ${done ? "text-saffron" : active ? "text-white" : "text-stone-500"}`}>{s.label}</p>
          </li>
        );
      })}
    </ol>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-baseline justify-between text-sm font-semibold text-stone-200">
        {label}
        {hint && <span className="text-xs font-normal text-stone-500">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

const inputCls =
  "w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white placeholder-stone-500 outline-none transition focus:border-saffron/70 focus:ring-2 focus:ring-saffron/20";

export default function App() {
  const [characters, setCharacters] = useState([]);
  const [shop, setShop] = useState("");
  const [dishes, setDishes] = useState("");
  const [language, setLanguage] = useState("tamil");
  const [character, setCharacter] = useState("arun");
  const [job, setJob] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState([]);
  const [showScript, setShowScript] = useState(false);
  const timer = useRef(null);

  const loadHistory = useCallback(() => {
    api("/api/reviews").then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    api("/api/characters").then(setCharacters).catch(() => setError("Cannot reach the backend. Is it running?"));
    loadHistory();
  }, [loadHistory]);

  const poll = useCallback((id) => {
    clearTimeout(timer.current);
    const tick = async () => {
      try {
        const s = await api(`/api/status/${id}`);
        setJob(s);
        if (s.status === "done" || s.status === "failed") {
          loadHistory();
          return;
        }
      } catch (e) {
        setError(e.message);
      }
      timer.current = setTimeout(tick, 2000);
    };
    tick();
  }, [loadHistory]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const running = job && (job.status === "queued" || job.status === "running");

  async function submit(e) {
    e.preventDefault();
    setError("");
    setShowScript(false);
    setSubmitting(true);
    try {
      const res = await api("/api/generate-review", {
        method: "POST",
        body: JSON.stringify({ shop, dishes, language, character }),
      });
      setJob({ id: res.id, status: "queued", progress: 0, stage: "Queued" });
      poll(res.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  function openPast(id) {
    setError("");
    setShowScript(false);
    poll(id);
  }

  const script = job?.script;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-12">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-stone-300">
            <span className="h-1.5 w-1.5 rounded-full bg-saffron" /> Free &amp; open-source stack
          </p>
          <h1 className="font-display text-3xl font-extrabold leading-tight sm:text-5xl">
            AI Food Reviewer <span className="text-chili">Engine</span>
          </h1>
          <p className="mt-2 max-w-xl text-sm text-stone-400">
            Paste a shop name or Google Maps link. Get a vlog-style script, voiceover and a captioned vertical video starring an
            AI character.
          </p>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        {/* ------------------------------------------------ left column */}
        <div className="space-y-6">
          <form onSubmit={submit} className="space-y-5 rounded-2xl border border-white/10 bg-white/[0.04] p-5 sm:p-6">
            <Field label="Shop name, location or Google Maps link" hint="e.g. Ambur Star Biryani, Anna Nagar">
              <input
                className={inputCls}
                value={shop}
                onChange={(e) => setShop(e.target.value)}
                placeholder="https://maps.app.goo.gl/… or Shop Name, Area"
                required
                minLength={2}
                disabled={running}
              />
            </Field>

            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="Dishes to feature" hint="optional">
                <input
                  className={inputCls}
                  value={dishes}
                  onChange={(e) => setDishes(e.target.value)}
                  placeholder="chicken biryani, parotta"
                  disabled={running}
                />
              </Field>
              <Field label="Language">
                <div className="grid grid-cols-2 gap-2 rounded-xl bg-white/5 p-1">
                  {[["tamil", "தமிழ் Tamil"], ["english", "English"]].map(([val, label]) => (
                    <button
                      type="button"
                      key={val}
                      onClick={() => setLanguage(val)}
                      disabled={running}
                      className={`rounded-lg px-3 py-2 text-sm font-semibold transition ${
                        language === val ? "bg-saffron text-ink" : "text-stone-300 hover:bg-white/10"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </Field>
            </div>

            <div>
              <span className="mb-2 block text-sm font-semibold text-stone-200">Choose your AI character</span>
              <div className="grid gap-3 sm:grid-cols-3">
                {characters.map((c) => (
                  <button
                    type="button"
                    key={c.id}
                    onClick={() => setCharacter(c.id)}
                    disabled={running}
                    className={`flex items-center gap-3 rounded-xl border p-3 text-left transition ${
                      character === c.id ? "border-saffron bg-saffron/10" : "border-white/10 bg-white/5 hover:border-white/25"
                    }`}
                  >
                    <CharacterAvatar color={c.color} name={c.name} />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-bold">{c.name}</span>
                      <span className="block truncate text-xs text-stone-400">{c.tagline}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {error && <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}

            <button
              type="submit"
              disabled={running || submitting || shop.trim().length < 2}
              className="w-full rounded-xl bg-chili px-5 py-3.5 text-sm font-bold text-white shadow-lg shadow-chili/20 transition hover:bg-[#ff5a40] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {running ? "Generating… keep this tab open" : "Generate review video"}
            </button>
          </form>

          {/* ---------------------------------------------- progress */}
          {job && (
            <section className="space-y-4 rounded-2xl border border-white/10 bg-white/[0.04] p-5 sm:p-6" aria-live="polite">
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-display text-lg font-bold">
                  {job.status === "done" ? "Your review is ready" : job.status === "failed" ? "Generation failed" : "Generating"}
                </h2>
                <span className="text-sm tabular-nums text-stone-400">{job.progress ?? 0}%</span>
              </div>
              <div className="h-3 overflow-hidden rounded-full bg-white/10">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    job.status === "failed" ? "bg-red-500" : job.status === "done" ? "bg-saffron" : "progress-stripes bg-chili"
                  }`}
                  style={{ width: `${Math.max(4, job.progress ?? 0)}%` }}
                />
              </div>
              <Stepper progress={job.progress ?? 0} failed={job.status === "failed"} />
              <p className="text-sm text-stone-400">{job.status === "failed" ? job.error : job.stage}</p>
            </section>
          )}

          {/* ---------------------------------------------- script */}
          {script && (
            <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 sm:p-6">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-display text-lg font-bold">{script.title}</h2>
                  <p className="text-xs text-stone-400">
                    {job.shop_name}
                    {job.shop_area ? ` · ${job.shop_area}` : ""} · {script.rating} / 5 · script by {script.generator}
                  </p>
                </div>
                <button
                  onClick={() => setShowScript((v) => !v)}
                  className="shrink-0 rounded-lg border border-white/15 px-3 py-1.5 text-xs font-semibold hover:bg-white/10"
                >
                  {showScript ? "Hide" : "Show"} script
                </button>
              </div>
              {script.warnings?.length > 0 && (
                <ul className="mt-3 space-y-1 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">
                  {script.warnings.map((w) => (
                    <li key={w}>• {w}</li>
                  ))}
                </ul>
              )}
              {showScript && (
                <div className="mt-4 space-y-3">
                  {script.scenes.map((s) => (
                    <div key={s.id} className="rounded-xl bg-white/5 p-3">
                      <p className="mb-1 text-[11px] font-bold uppercase tracking-wider text-saffron">{s.id}</p>
                      <p className="text-sm leading-relaxed text-stone-200">{s.text}</p>
                    </div>
                  ))}
                  <p className="text-xs text-stone-500">{script.hashtags?.join(" ")}</p>
                </div>
              )}
            </section>
          )}
        </div>

        {/* ------------------------------------------------ right column */}
        <aside className="space-y-6">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
            <div className="mx-auto aspect-[9/16] w-full max-w-[300px] overflow-hidden rounded-xl bg-black/60 ring-1 ring-white/10">
              {job?.status === "done" ? (
                <video key={job.video_url} className="h-full w-full object-contain" src={`${API}${job.video_url}`} poster={`${API}${job.poster_url}`} controls playsInline />
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-stone-500">
                  <svg viewBox="0 0 24 24" className="h-10 w-10" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <rect x="3" y="5" width="18" height="14" rx="3" />
                    <path d="M10 9.5v5l4.5-2.5z" fill="currentColor" />
                  </svg>
                  <p className="text-sm">{running ? "Rendering your video…" : "Your video preview will appear here"}</p>
                </div>
              )}
            </div>
            {job?.status === "done" && (
              <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs font-semibold">
                <a className="rounded-lg bg-saffron px-2 py-2 text-ink hover:brightness-110" href={`${API}${job.video_url}`} download>
                  Video .mp4
                </a>
                <a className="rounded-lg bg-white/10 px-2 py-2 hover:bg-white/20" href={`${API}${job.audio_url}`} download>
                  Audio .mp3
                </a>
                <a className="rounded-lg bg-white/10 px-2 py-2 hover:bg-white/20" href={`${API}${job.subtitles_url}`} download>
                  Subs .srt
                </a>
              </div>
            )}
            <p className="mt-3 text-center text-[11px] text-stone-500">
              Videos are labelled “AI-generated”. Don’t present the AI character as a real person.
            </p>
          </div>

          {history.length > 0 && (
            <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
              <h3 className="mb-3 text-sm font-bold">Recent reviews</h3>
              <ul className="space-y-2">
                {history.map((h) => (
                  <li key={h.id}>
                    <button
                      onClick={() => openPast(h.id)}
                      className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-white/5"
                    >
                      <span className="h-12 w-8 shrink-0 overflow-hidden rounded bg-white/10">
                        {h.poster_url && <img src={`${API}${h.poster_url}`} alt="" className="h-full w-full object-cover" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold">{h.shop_name}</span>
                        <span className="block text-xs capitalize text-stone-500">
                          {h.status} · {h.language}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
