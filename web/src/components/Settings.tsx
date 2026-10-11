import { useState } from "react";
import type { SettingsState } from "../types";

interface Props {
  settings: SettingsState;
  onUpdateSettings: (newSettings: SettingsState, quiet?: boolean) => void;
  onClearHistory: () => void;
  historyCount: number;
  onCopy: (text: string) => void;
}

export function Settings({
  settings,
  onUpdateSettings,
  onClearHistory,
  historyCount,
  onCopy,
}: Props) {
  const [revealed, setRevealed] = useState(false);
  const [clrArmed, setClrArmed] = useState(false);

  const handleChainChange = (newChain: string) => {
    onUpdateSettings({ ...settings, chain: newChain });
  };

  const handleThresholdChange = (newThr: number) => {
    onUpdateSettings({ ...settings, thr: newThr }, true);
  };

  const handleFxChange = (newFx: boolean) => {
    onUpdateSettings({ ...settings, fx: newFx });
  };

  const handleRegenerateKey = () => {
    const randomHex = [...Array(20)]
      .map(() => Math.floor(Math.random() * 16).toString(16))
      .join("");
    const newKey = "rs_live_" + randomHex;
    onUpdateSettings({ ...settings, key: newKey });
  };

  const handleClearClick = () => {
    if (!clrArmed) {
      setClrArmed(true);
      setTimeout(() => {
        setClrArmed(false);
      }, 3000);
    } else {
      setClrArmed(false);
      onClearHistory();
    }
  };

  const maskedKey = revealed
    ? settings.key
    : settings.key.slice(0, 8) + "••••••••••••";

  return (
    <div className="view" id="v-settings">
      <section className="card">
        <h3>Settings</h3>

        <div className="srow">
          <div>
            <b>Default chain</b>
            <small>Used by the scanner form</small>
          </div>
          <select
            id="st-ch"
            aria-label="Default chain"
            value={settings.chain}
            onChange={(e) => handleChainChange(e.target.value)}
          >
            {["BSC", "ETH", "Base"].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="srow">
          <div>
            <b>Alert threshold</b>
            <small>
              Flag scans at or above this score in history ·{" "}
              <span id="st-tv">{settings.thr}</span>/100
            </small>
          </div>
          <input
            id="st-th"
            type="range"
            min={0}
            max={100}
            value={settings.thr}
            style={{ width: "200px" }}
            aria-label="Alert threshold"
            onChange={(e) => handleThresholdChange(Number(e.target.value))}
          />
        </div>

        <div className="srow">
          <div>
            <b>Background effects</b>
            <small>Hexagonal grid behind the dashboard</small>
          </div>
          <label className="sw">
            <input
              id="st-fx"
              type="checkbox"
              aria-label="Background effects"
              checked={settings.fx}
              onChange={(e) => handleFxChange(e.target.checked)}
            />
            <i />
          </label>
        </div>

        <div className="srow">
          <div>
            <b>API key</b>
            <small>{maskedKey}</small>
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              className="ghost sm"
              data-reveal="1"
              onClick={() => setRevealed(!revealed)}
            >
              {revealed ? "Hide" : "Reveal"}
            </button>
            <button
              className="ghost sm"
              data-cp="key"
              onClick={() => onCopy(settings.key)}
            >
              Copy
            </button>
            <button
              className="ghost sm"
              data-regen="1"
              onClick={handleRegenerateKey}
            >
              Regenerate
            </button>
          </div>
        </div>

        <div className="srow" style={{ border: 0 }}>
          <div>
            <b>Clear scan history</b>
            <small>Removes all {historyCount} saved scans</small>
          </div>
          <button
            className="ghost sm"
            data-clr="1"
            style={{ borderColor: "#ff4a5e88", color: "#ffb3bb" }}
            onClick={handleClearClick}
          >
            {clrArmed ? "Click again to confirm" : "Clear history"}
          </button>
        </div>
      </section>
    </div>
  );
}
