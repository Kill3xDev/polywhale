import { useState, useEffect, useCallback, useRef } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine } from "recharts";

const API = "https://polywhale-production.up.railway.app/api";
const WS_URL = "wss://polywhale-production.up.railway.app/ws";

const fmt$   = (n) => n == null ? "—" : `$${Number(n).toLocaleString(undefined, {maximumFractionDigits:0})}`;
const fmtPct = (n) => n == null ? "—" : `${(Number(n)*100).toFixed(1)}%`;
const fmtTs  = (ts) => { if (!ts) return "—"; const d = new Date(ts*1000); return d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit"}); };
const shortW = (w) => w ? `${w.slice(0,6)}…${w.slice(-4)}` : "—";

const scoreColor = (s) => {
  if (s >= 80) return "#ff2d55";
  if (s >= 60) return "#ff6b35";
  if (s >= 40) return "#ff9f0a";
  if (s >= 25) return "#30d158";
  return "#636366";
};

const SIGNAL_META = {
  IMPACT_TRADE:          { icon:"💥", label:"Impact Trade",       desc:"Large order walking the book — paying for immediacy" },
  PRICE_SHOCK:           { icon:"⚡", label:"Price Shock",         desc:"Rapid probability shift in short window" },
  FOLLOW_THROUGH:        { icon:"📈", label:"Follow-Through",      desc:"Post-shock momentum or mean reversion" },
  OB_IMBALANCE:          { icon:"📊", label:"Book Imbalance",      desc:"Bid/ask depth pressure near mid" },
  SPREAD_FRAGILITY:      { icon:"🕸️", label:"Spread Fragility",    desc:"Wide spread + thin depth = fragile market" },
  ARB_VIOLATION:         { icon:"⚖️", label:"Arb Violation",       desc:"Related market probabilities violate logical bounds" },
  TIME_DECAY_DRIFT:      { icon:"⏱️", label:"Time Decay Drift",    desc:"Prob rising on dated market with no apparent news" },
  SNAPBACK:              { icon:"↩️", label:"Snapback",            desc:"Price shock reversing — mean reversion signal" },
  SMART_WALLET:          { icon:"🧠", label:"Smart Wallet",        desc:"High-accuracy wallet making coordinated moves" },
  CROSS_VENUE:           { icon:"🌐", label:"Cross-Venue",         desc:"Polymarket diverges from external sources" },
  VOL_REGIME:            { icon:"📡", label:"Vol Regime Shift",    desc:"Volume z-score spike — unusual activity level" },
  ROUND_NUM_ANCHOR:      { icon:"🎯", label:"Round # Anchor",      desc:"Price stuck at round number — behavioral bias" },
  EXTREME_PROB:          { icon:"🔴", label:"Extreme Prob",        desc:"Near 0% or 100% — possible over/underpricing" },
  HEADLINE_OVERREACTION: { icon:"📰", label:"Headline Reaction",   desc:"Big spike now stalling — possible overreaction fade" },
  COMPOSITE:             { icon:"🔮", label:"Composite",           desc:"Multiple signals firing simultaneously" },
  LARGE_TRADE:           { icon:"🐋", label:"Large Trade",         desc:"Single trade above whale threshold" },
  RAPID_SHIFT:           { icon:"⚡", label:"Rapid Shift",         desc:"Probability moved rapidly" },
  IMBALANCE:             { icon:"📊", label:"Imbalance",           desc:"Order book imbalance detected" },
  CROSS_MARKET:          { icon:"🕸️", label:"Cross Market",        desc:"Same wallet in multiple markets" },
};

const getMeta = (type) => SIGNAL_META[type] || { icon:"🚨", label: type, desc: "" };

function ScoreRing({ score }) {
  const r = 20, circ = 2*Math.PI*r;
  const fill = (score/100)*circ;
  const color = scoreColor(score);
  return (
    <svg width="52" height="52" style={{flexShrink:0}}>
      <circle cx="26" cy="26" r={r} fill="none" stroke="#2c2c2e" strokeWidth="4"/>
      <circle cx="26" cy="26" r={r} fill="none" stroke={color} strokeWidth="4"
        strokeDasharray={`${fill} ${circ}`} strokeLinecap="round"
        transform="rotate(-90 26 26)" style={{transition:"stroke-dasharray 0.5s ease"}}/>
      <text x="26" y="31" textAnchor="middle" fill={color}
        style={{fontSize:"13px", fontWeight:700, fontFamily:"'JetBrains Mono',monospace"}}>{score}</text>
    </svg>
  );
}

function ProbBar({ prob }) {
  const pct = prob == null ? 50 : prob*100;
  const color = pct > 60 ? "#30d158" : pct < 40 ? "#ff453a" : "#ff9f0a";
  return (
    <div style={{display:"flex", alignItems:"center", gap:8, minWidth:120}}>
      <div style={{flex:1, height:6, background:"#2c2c2e", borderRadius:3, overflow:"hidden"}}>
        <div style={{width:`${pct}%`, height:"100%", background:color, borderRadius:3, transition:"width 0.4s ease"}}/>
      </div>
      <span style={{fontFamily:"'JetBrains Mono',monospace", fontSize:12, color, minWidth:38}}>{pct.toFixed(1)}%</span>
    </div>
  );
}

function SignalBadge({ type }) {
  const m = getMeta(type);
  const color = type === "COMPOSITE" ? "#bf5af2" :
                ["IMPACT_TRADE","SMART_WALLET"].includes(type) ? "#ff2d55" :
                ["PRICE_SHOCK","FOLLOW_THROUGH"].includes(type) ? "#ff9f0a" :
                ["ARB_VIOLATION","CROSS_VENUE"].includes(type) ? "#64d2ff" : "#636366";
  return (
    <span title={m.desc} style={{
      fontSize:10, fontWeight:700, letterSpacing:"0.06em",
      color, background:`${color}18`, padding:"2px 7px", borderRadius:20,
      display:"inline-flex", alignItems:"center", gap:4,
    }}>
      {m.icon} {m.label.toUpperCase()}
    </span>
  );
}

function AlertCard({ alert }) {
  const [expanded, setExpanded] = useState(false);
  let extra = {};
  try { extra = JSON.parse(alert.extra || "{}"); } catch {}

  return (
    <div onClick={() => setExpanded(!expanded)}
      style={{
        background:"#1c1c1e", borderRadius:12, padding:"14px 16px", marginBottom:8,
        border:`1px solid ${scoreColor(alert.insider_score)}22`,
        cursor:"pointer", transition:"border-color 0.2s ease",
        animation:"fadeIn 0.3s ease",
      }}>
      <div style={{display:"flex", gap:14, alignItems:"flex-start"}}>
        <ScoreRing score={alert.insider_score||0}/>
        <div style={{flex:1, minWidth:0}}>
          <div style={{display:"flex", alignItems:"center", gap:8, flexWrap:"wrap", marginBottom:4}}>
            <SignalBadge type={alert.alert_type}/>
            <span style={{fontSize:11, color:"#636366", fontFamily:"monospace"}}>{fmtTs(alert.timestamp)}</span>
          </div>
          <div style={{fontWeight:600, fontSize:13, color:"#f2f2f7", marginBottom:4,
            overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>
            {alert.question || alert.market_id}
          </div>
          <div style={{fontSize:12, color:"#aeaeb2", marginBottom:6}}>{alert.description}</div>
          <div style={{display:"flex", gap:14, flexWrap:"wrap"}}>
            {alert.usd_value && <span style={{fontSize:11, color:"#64d2ff"}}>💰 {fmt$(alert.usd_value)}</span>}
            {alert.prob_before != null && alert.prob_after != null && (
              <span style={{fontSize:11, color:"#ffd60a"}}>
                📈 {fmtPct(alert.prob_before)} → {fmtPct(alert.prob_after)}
              </span>
            )}
            {alert.wallet && <span style={{fontSize:11, color:"#bf5af2", fontFamily:"monospace"}}>👤 {shortW(alert.wallet)}</span>}
            {alert.polymarket_url && (
              <a href={alert.polymarket_url} target="_blank" rel="noreferrer"
                onClick={e=>e.stopPropagation()} style={{fontSize:11, color:"#0a84ff"}}>🔗 View</a>
            )}
          </div>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && Object.keys(extra).length > 0 && (
        <div style={{marginTop:12, paddingTop:12, borderTop:"1px solid #2c2c2e"}}>
          <div style={{fontSize:10, color:"#636366", marginBottom:6, letterSpacing:"0.06em"}}>SIGNAL DETAILS</div>
          <div style={{display:"flex", flexWrap:"wrap", gap:8}}>
            {Object.entries(extra).map(([k,v]) => (
              <div key={k} style={{
                background:"#2c2c2e", borderRadius:6, padding:"4px 10px",
                fontSize:11, color:"#aeaeb2",
              }}>
                <span style={{color:"#636366"}}>{k}: </span>
                <span style={{color:"#f2f2f7", fontFamily:"monospace"}}>{String(v)}</span>
              </div>
            ))}
          </div>
          <div style={{marginTop:8, fontSize:10, color:"#3a3a3c", fontStyle:"italic"}}>
            {getMeta(alert.alert_type).desc}
          </div>
        </div>
      )}
    </div>
  );
}

function MarketRow({ market, onClick, selected }) {
  return (
    <div onClick={() => onClick(market)}
      style={{
        padding:"12px 16px", cursor:"pointer",
        background: selected ? "#0a84ff18" : "transparent",
        borderBottom:"1px solid #2c2c2e",
        borderLeft: selected ? "3px solid #0a84ff" : "3px solid transparent",
        transition:"all 0.15s ease",
      }}>
      <div style={{fontWeight:600, fontSize:13, color:"#f2f2f7", marginBottom:6,
        overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>{market.question}</div>
      <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", gap:8}}>
        <ProbBar prob={market.last_prob}/>
        <div style={{display:"flex", gap:8, alignItems:"center"}}>
          <span style={{fontSize:11, color:"#636366"}}>{fmt$(market.volume_24h)}</span>
          {market.alert_count > 0 && (
            <span style={{
              fontSize:10, fontWeight:700,
              background: scoreColor(market.max_score||0),
              color:"#000", borderRadius:20, padding:"1px 7px",
            }}>{market.alert_count}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function WalletRow({ wallet }) {
  const score = wallet.win_rate ? Math.round(wallet.win_rate * 100) : 0;
  return (
    <div style={{
      display:"grid", gridTemplateColumns:"1fr 100px 70px 70px 60px",
      padding:"10px 16px", borderBottom:"1px solid #2c2c2e",
      fontSize:12, alignItems:"center", gap:8,
    }}>
      <span style={{fontFamily:"monospace", color:"#bf5af2"}}>{shortW(wallet.address)}</span>
      <span style={{color:"#64d2ff"}}>{fmt$(wallet.total_volume_usd)}</span>
      <span style={{color:"#aeaeb2", textAlign:"center"}}>{wallet.trade_count}</span>
      <span style={{
        color: score > 60 ? "#30d158" : score > 40 ? "#ff9f0a" : "#636366",
        textAlign:"center", fontFamily:"monospace",
      }}>{wallet.win_rate ? `${score}%` : "—"}</span>
      <span style={{color: wallet.watchlist ? "#ffd60a" : "#636366", textAlign:"center"}}>
        {wallet.watchlist ? "★" : "☆"}
      </span>
    </div>
  );
}

function MarketChart({ market, allAlerts }) {
  const [history, setHistory] = useState([]);

  useEffect(() => {
    if (!market) return;
    fetch(`${API}/markets/${market.id}/history?hours=24`)
      .then(r=>r.json())
      .then(d => setHistory(d.map(p => ({
        t: fmtTs(p.timestamp),
        ts: p.timestamp,
        prob: +(p.prob*100).toFixed(2),
      }))))
      .catch(()=>{});
  }, [market?.id]);

  const mktAlerts = allAlerts.filter(a => a.market_id === market?.id);

  if (!market) return (
    <div style={{display:"flex", alignItems:"center", justifyContent:"center",
      height:"100%", color:"#636366", fontSize:14}}>Select a market to view its chart</div>
  );

  return (
    <div>
      <div style={{fontWeight:700, fontSize:15, color:"#f2f2f7", marginBottom:4}}>{market.question}</div>
      <div style={{display:"flex", gap:16, marginBottom:16, flexWrap:"wrap"}}>
        <ProbBar prob={market.last_prob}/>
        <span style={{fontSize:12, color:"#636366"}}>24h Vol: {fmt$(market.volume_24h)}</span>
        {market.polymarket_url && (
          <a href={market.polymarket_url} target="_blank" rel="noreferrer"
            style={{fontSize:12, color:"#0a84ff"}}>Open on Polymarket ↗</a>
        )}
      </div>
      {history.length > 1 ? (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={history}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2c2c2e"/>
            <XAxis dataKey="t" tick={{fontSize:10, fill:"#636366"}} interval="preserveStartEnd"/>
            <YAxis domain={[0,100]} tick={{fontSize:10, fill:"#636366"}} unit="%"/>
            <Tooltip
              contentStyle={{background:"#1c1c1e", border:"1px solid #3a3a3c", borderRadius:8}}
              labelStyle={{color:"#aeaeb2"}} itemStyle={{color:"#0a84ff"}}
              formatter={(v)=>[`${v}%`, "YES Prob"]}/>
            <Line type="monotone" dataKey="prob" stroke="#0a84ff" strokeWidth={2}
              dot={false} activeDot={{r:4}}/>
          </LineChart>
        </ResponsiveContainer>
      ) : (
        <div style={{height:220, display:"flex", alignItems:"center", justifyContent:"center",
          color:"#636366", fontSize:13, border:"1px dashed #3a3a3c", borderRadius:8}}>
          Collecting probability history…
        </div>
      )}
      {mktAlerts.length > 0 && (
        <div style={{marginTop:20}}>
          <div style={{fontSize:11, color:"#636366", marginBottom:10, letterSpacing:"0.05em", fontWeight:700}}>
            ALERTS FOR THIS MARKET ({mktAlerts.length})
          </div>
          {mktAlerts.slice(0,5).map((a,i) => <AlertCard key={a.id??i} alert={a}/>)}
        </div>
      )}
    </div>
  );
}

function SignalLegend() {
  return (
    <div style={{background:"#1c1c1e", borderRadius:12, padding:20, border:"1px solid #2c2c2e"}}>
      <div style={{fontSize:12, fontWeight:700, color:"#636366", letterSpacing:"0.06em", marginBottom:14}}>
        12-SIGNAL DETECTION ENGINE
      </div>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:8}}>
        {Object.entries(SIGNAL_META).filter(([k]) => !["RAPID_SHIFT","IMBALANCE","CROSS_MARKET","LARGE_TRADE"].includes(k))
          .map(([type, meta]) => (
          <div key={type} style={{display:"flex", gap:8, padding:"6px 8px",
            background:"#2c2c2e", borderRadius:8, alignItems:"flex-start"}}>
            <span style={{fontSize:16, flexShrink:0}}>{meta.icon}</span>
            <div>
              <div style={{fontSize:11, fontWeight:700, color:"#f2f2f7"}}>{meta.label}</div>
              <div style={{fontSize:10, color:"#636366", lineHeight:1.3}}>{meta.desc}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatsBar({ stats }) {
  const items = [
    { label:"Markets",     value: stats.markets ?? "…" },
    { label:"Trades",      value: stats.trades ?? "…" },
    { label:"Wallets",     value: stats.wallets ?? "…" },
    { label:"Alerts",      value: stats.alerts ?? "…" },
    { label:"Alerts (1h)", value: stats.alerts_last_hour ?? "…", highlight: stats.alerts_last_hour > 0 },
  ];
  return (
    <div style={{display:"flex", gap:1, background:"#2c2c2e", borderRadius:10, overflow:"hidden", marginBottom:20}}>
      {items.map(item => (
        <div key={item.label} style={{flex:1, padding:"12px 8px", textAlign:"center", background:"#1c1c1e",
          borderRight:"1px solid #2c2c2e"}}>
          <div style={{fontSize:20, fontWeight:800, fontFamily:"'JetBrains Mono',monospace",
            color: item.highlight ? "#ff9f0a" : "#f2f2f7"}}>{item.value}</div>
          <div style={{fontSize:10, color:"#636366", letterSpacing:"0.06em", textTransform:"uppercase"}}>{item.label}</div>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [tab, setTab]         = useState("alerts");
  const [markets, setMarkets] = useState([]);
  const [alerts2, setAlerts]  = useState([]);
  const [wallets, setWallets] = useState([]);
  const [stats, setStats]     = useState({});
  const [selectedMkt, setSelectedMkt] = useState(null);
  const [liveAlerts, setLiveAlerts]   = useState([]);
  const [wsAlive, setWsAlive]         = useState(false);
  const [minScore, setMinScore]       = useState(0);
  const [signalFilter, setSignalFilter] = useState("ALL");
  const wsRef = useRef(null);

  const fetchAll = useCallback(async () => {
    try {
      const [m, a, w, s] = await Promise.all([
        fetch(`${API}/markets?limit=100`).then(r=>r.json()),
        fetch(`${API}/alerts?limit=200&min_score=${minScore}`).then(r=>r.json()),
        fetch(`${API}/wallets?limit=50`).then(r=>r.json()),
        fetch(`${API}/stats`).then(r=>r.json()),
      ]);
      setMarkets(m); setAlerts(a); setWallets(w); setStats(s);
    } catch(e) { console.error(e); }
  }, [minScore]);

  useEffect(() => { fetchAll(); const iv = setInterval(fetchAll, 15000); return () => clearInterval(iv); }, [fetchAll]);

  useEffect(() => {
    const connect = () => {
      try {
        const ws = new WebSocket(WS_URL);
        ws.onopen  = () => setWsAlive(true);
        ws.onclose = () => { setWsAlive(false); setTimeout(connect, 3000); };
        ws.onerror = () => ws.close();
        ws.onmessage = (e) => {
          try {
            const na = JSON.parse(e.data);
            if (na.length > 0) setLiveAlerts(prev => [...na, ...prev].slice(0,100));
          } catch {}
        };
        wsRef.current = ws;
      } catch {}
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  const allAlerts = [...liveAlerts, ...alerts2]
    .filter((a,i,arr) => arr.findIndex(x=>x.id===a.id)===i)
    .sort((a,b) => b.timestamp - a.timestamp);

  const signalTypes = ["ALL", ...new Set(allAlerts.map(a=>a.alert_type))];

  const filteredAlerts = allAlerts
    .filter(a => a.insider_score >= minScore)
    .filter(a => signalFilter === "ALL" || a.alert_type === signalFilter);

  const tabStyle = (t) => ({
    padding:"8px 18px", borderRadius:8, cursor:"pointer", fontSize:13, fontWeight:600,
    background: tab===t ? "#0a84ff" : "transparent",
    color: tab===t ? "#fff" : "#aeaeb2",
    border:"none", transition:"all 0.15s ease",
  });

  return (
    <div style={{minHeight:"100vh", background:"#000", fontFamily:"'SF Pro Display','Helvetica Neue',system-ui,sans-serif", color:"#f2f2f7"}}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');
        @keyframes fadeIn { from{opacity:0;transform:translateY(-6px)} to{opacity:1;transform:none} }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        * { box-sizing:border-box; margin:0; padding:0; }
        ::-webkit-scrollbar { width:4px; } ::-webkit-scrollbar-track { background:#1c1c1e; }
        ::-webkit-scrollbar-thumb { background:#3a3a3c; border-radius:2px; } a { text-decoration:none; }
      `}</style>

      {/* Header */}
      <div style={{borderBottom:"1px solid #1c1c1e", padding:"16px 24px",
        display:"flex", alignItems:"center", justifyContent:"space-between",
        background:"rgba(0,0,0,0.8)", backdropFilter:"blur(20px)",
        position:"sticky", top:0, zIndex:100}}>
        <div style={{display:"flex", alignItems:"center", gap:14}}>
          <div style={{fontSize:24}}>🐋</div>
          <div>
            <div style={{fontWeight:800, fontSize:18, letterSpacing:"-0.02em"}}>PolyWhale</div>
            <div style={{fontSize:11, color:"#636366", letterSpacing:"0.05em"}}>12-SIGNAL DETECTION ENGINE</div>
          </div>
        </div>
        <div style={{display:"flex", alignItems:"center", gap:8}}>
          <div style={{width:8, height:8, borderRadius:"50%",
            background: wsAlive ? "#30d158" : "#636366",
            boxShadow: wsAlive ? "0 0 8px #30d158" : "none",
            animation: wsAlive ? "pulse 1.5s infinite" : "none"}}/>
          <span style={{fontSize:11, color: wsAlive ? "#30d158" : "#636366", fontFamily:"monospace"}}>
            {wsAlive ? "LIVE" : "CONNECTING"}
          </span>
        </div>
      </div>

      <div style={{padding:"24px", maxWidth:1500, margin:"0 auto"}}>
        <StatsBar stats={stats}/>

        <div style={{display:"flex", gap:4, marginBottom:20, background:"#1c1c1e",
          padding:4, borderRadius:10, width:"fit-content"}}>
          {[["alerts","🚨 Alerts"],["markets","📊 Markets"],["wallets","👤 Wallets"],["legend","📖 Signals"]].map(([t,label]) => (
            <button key={t} onClick={()=>setTab(t)} style={tabStyle(t)}>{label}</button>
          ))}
        </div>

        {/* ALERTS TAB */}
        {tab === "alerts" && (
          <div style={{display:"grid", gridTemplateColumns:"1fr 280px", gap:20}}>
            <div>
              <div style={{display:"flex", gap:12, marginBottom:16, alignItems:"center", flexWrap:"wrap"}}>
                <div>
                  <label style={{fontSize:11, color:"#636366", marginRight:6}}>MIN SCORE</label>
                  <select value={minScore} onChange={e=>setMinScore(+e.target.value)}
                    style={{background:"#1c1c1e", color:"#f2f2f7", border:"1px solid #3a3a3c",
                      borderRadius:6, padding:"4px 8px", fontSize:12}}>
                    {[0,25,40,60,75].map(v=><option key={v} value={v}>{v}+</option>)}
                  </select>
                </div>
                <div>
                  <label style={{fontSize:11, color:"#636366", marginRight:6}}>SIGNAL</label>
                  <select value={signalFilter} onChange={e=>setSignalFilter(e.target.value)}
                    style={{background:"#1c1c1e", color:"#f2f2f7", border:"1px solid #3a3a3c",
                      borderRadius:6, padding:"4px 8px", fontSize:12}}>
                    {signalTypes.map(t=><option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <span style={{fontSize:12, color:"#636366"}}>{filteredAlerts.length} alerts</span>
                {liveAlerts.length > 0 && (
                  <span style={{fontSize:11, background:"#ff453a22", color:"#ff453a",
                    padding:"2px 8px", borderRadius:20, fontWeight:700}}>+{liveAlerts.length} live</span>
                )}
              </div>
              {filteredAlerts.length === 0 ? (
                <div style={{textAlign:"center", padding:"60px 0", color:"#636366",
                  border:"1px dashed #2c2c2e", borderRadius:12}}>
                  <div style={{fontSize:48, marginBottom:12}}>🔍</div>
                  <div style={{fontWeight:600}}>No alerts yet</div>
                  <div style={{fontSize:13, marginTop:6}}>Monitoring {stats.markets||0} markets with 12 signals</div>
                </div>
              ) : filteredAlerts.slice(0,100).map((a,i) => <AlertCard key={a.id??i} alert={a}/>)}
            </div>

            {/* Signal breakdown sidebar */}
            <div style={{background:"#1c1c1e", borderRadius:12, padding:16,
              border:"1px solid #2c2c2e", height:"fit-content", position:"sticky", top:80}}>
              <div style={{fontSize:11, color:"#636366", letterSpacing:"0.06em", fontWeight:700, marginBottom:12}}>
                SIGNAL BREAKDOWN
              </div>
              {Object.entries(
                allAlerts.reduce((acc, a) => {
                  acc[a.alert_type] = (acc[a.alert_type]||0) + 1;
                  return acc;
                }, {})
              ).sort((a,b)=>b[1]-a[1]).map(([type, count]) => {
                const m = getMeta(type);
                return (
                  <div key={type}
                    onClick={() => setSignalFilter(signalFilter===type ? "ALL" : type)}
                    style={{
                      display:"flex", alignItems:"center", gap:8, padding:"6px 8px",
                      borderRadius:8, cursor:"pointer", marginBottom:4,
                      background: signalFilter===type ? "#0a84ff22" : "transparent",
                      transition:"background 0.15s ease",
                    }}>
                    <span style={{fontSize:14}}>{m.icon}</span>
                    <span style={{fontSize:11, color:"#aeaeb2", flex:1}}>{m.label}</span>
                    <span style={{fontSize:11, fontFamily:"monospace", color:"#636366",
                      background:"#2c2c2e", padding:"1px 7px", borderRadius:20}}>{count}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* MARKETS TAB */}
        {tab === "markets" && (
          <div style={{display:"grid", gridTemplateColumns:"320px 1fr", gap:16, minHeight:500}}>
            <div style={{background:"#1c1c1e", borderRadius:12, overflow:"hidden",
              border:"1px solid #2c2c2e", maxHeight:"80vh", overflowY:"auto"}}>
              <div style={{padding:"12px 16px", borderBottom:"1px solid #2c2c2e",
                fontSize:11, color:"#636366", letterSpacing:"0.05em", fontWeight:700}}>
                ACTIVE MARKETS ({markets.length})
              </div>
              {markets.map(m => (
                <MarketRow key={m.id} market={m} onClick={setSelectedMkt} selected={selectedMkt?.id===m.id}/>
              ))}
            </div>
            <div style={{background:"#1c1c1e", borderRadius:12, padding:20, border:"1px solid #2c2c2e"}}>
              <MarketChart market={selectedMkt} allAlerts={allAlerts}/>
            </div>
          </div>
        )}

        {/* WALLETS TAB */}
        {tab === "wallets" && (
          <div style={{background:"#1c1c1e", borderRadius:12, overflow:"hidden", border:"1px solid #2c2c2e"}}>
            <div style={{display:"grid", gridTemplateColumns:"1fr 100px 70px 70px 60px",
              padding:"10px 16px", background:"#2c2c2e",
              fontSize:10, color:"#636366", letterSpacing:"0.07em", fontWeight:700, gap:8}}>
              <span>WALLET</span><span>VOLUME</span>
              <span style={{textAlign:"center"}}>TRADES</span>
              <span style={{textAlign:"center"}}>WIN %</span>
              <span style={{textAlign:"center"}}>WATCH</span>
            </div>
            {wallets.length === 0 ? (
              <div style={{padding:"40px", textAlign:"center", color:"#636366", fontSize:13}}>
                Wallets will appear as trades are ingested…
              </div>
            ) : wallets.map(w => <WalletRow key={w.address} wallet={w}/>)}
          </div>
        )}

        {/* LEGEND TAB */}
        {tab === "legend" && <SignalLegend/>}

        <div style={{marginTop:32, textAlign:"center", fontSize:11, color:"#3a3a3c"}}>
          12-signal engine · Polling every {10}s · Polymarket Gamma API & CLOB · Not financial advice
        </div>
      </div>
    </div>
  );
}
