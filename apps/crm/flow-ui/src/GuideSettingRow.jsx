import React, { useId, useState } from "react";
import { Icon } from "./ui.jsx";

// A recommendation is a target value, never a claim about the user's current setting.
export function SettingRow({ field, optional = false, value, choice = false, explanation, accessory, children }) {
  const [expanded, setExpanded] = useState(false);
  const helpId = useId();
  return <li className="meta-start-setting"><div className="meta-start-setting-content">
    <div className="meta-start-field">{field}{optional && <span className="meta-start-optional">（选填）</span>}</div>
    <div className="meta-start-setting-detail">
      <div className="meta-start-answer"><strong className={choice ? "meta-start-choice" : ""}>{value}</strong>
        {explanation && <button type="button" className="meta-setting-help-toggle" aria-expanded={expanded} aria-controls={helpId} onClick={() => setExpanded(!expanded)}><Icon name="info" size={14} />{explanation.title}</button>}
        {accessory}
      </div>
      {children}
      {explanation && <div id={helpId} className="meta-setting-help-content" hidden={!expanded}>{typeof explanation.content === "function" ? explanation.content(expanded) : explanation.content}</div>}
    </div>
  </div></li>;
}
