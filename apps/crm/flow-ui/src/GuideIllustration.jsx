import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "./ui.jsx";

export function GuideIllustration({ src, title, alt, caption, active = true }) {
  const id = useId();
  const root = useRef(null);
  const trigger = useRef(null);
  const panel = useRef(null);
  const closeTimer = useRef(null);
  const restoringFocus = useRef(false);
  const pinned = useRef(false);
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const cancelClose = () => clearTimeout(closeTimer.current);
  const reveal = () => { cancelClose(); if (active && !restoringFocus.current) setOpen(true); };
  const dismiss = (restoreFocus = false) => {
    cancelClose();
    pinned.current = false;
    setOpen(false);
    if (restoreFocus) {
      restoringFocus.current = true;
      trigger.current?.focus();
      restoringFocus.current = false;
    }
  };
  const leave = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      if (!pinned.current && !root.current?.contains(document.activeElement)) setOpen(false);
    }, 180);
  };

  useEffect(() => () => clearTimeout(closeTimer.current), []);
  useEffect(() => {
    if (!active) {
      clearTimeout(closeTimer.current);
      pinned.current = false;
      setOpen(false);
    }
  }, [active]);
  useLayoutEffect(() => {
    const popup = panel.current;
    if (!open || !active) {
      if (popup.matches(":popover-open")) popup.hidePopover();
      return;
    }
    popup.showPopover();
    const place = () => {
      const viewportWidth = document.documentElement.clientWidth;
      popup.style.maxWidth = `${Math.max(0, viewportWidth - 24)}px`;
      const rect = trigger.current.getBoundingClientRect();
      const width = popup.getBoundingClientRect().width;
      const height = popup.getBoundingClientRect().height;
      const margin = 12;
      const right = rect.right + width + 12 <= viewportWidth - margin;
      const left = right ? rect.right + 12 : Math.max(margin, Math.min(rect.left, viewportWidth - width - margin));
      const below = rect.bottom + 10 + height <= window.innerHeight - margin;
      const above = rect.top - height - 10 >= margin;
      const top = Math.max(margin, Math.min(right ? rect.top - 12 : above && !below ? rect.top - height - 10 : rect.bottom + 10, window.innerHeight - height - margin));
      popup.style.left = `${left}px`;
      popup.style.top = `${top}px`;
      popup.dataset.side = right ? "right" : below ? "below" : above ? "above" : "floating";
      popup.style.setProperty("--guide-arrow-y", `${Math.max(18, Math.min(rect.top + rect.height / 2 - top, height - 18))}px`);
      popup.style.setProperty("--guide-arrow-x", `${Math.max(18, Math.min(rect.left + rect.width / 2 - left, width - 18))}px`);
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(popup);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    const outside = (event) => { if (!root.current?.contains(event.target)) dismiss(); };
    const escape = (event) => {
      if (event.key === "Escape") { event.preventDefault(); dismiss(root.current?.contains(document.activeElement)); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
      if (popup.matches(":popover-open")) popup.hidePopover();
    };
  }, [open, active]);

  return <div ref={root} className="guide-illustration"
    onPointerEnter={(event) => { if (event.pointerType === "mouse" && window.innerWidth > 600) reveal(); }}
    onPointerLeave={leave}
    onBlur={(event) => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) dismiss(); }}>
    <button ref={trigger} type="button" className="guide-illustration-trigger" aria-label={`${title}图示`}
      aria-expanded={open && active} aria-controls={id} aria-haspopup="dialog"
      onFocus={(event) => { if (event.target.matches(":focus-visible")) reveal(); }}
      onClick={() => { pinned.current = true; reveal(); }}>
      <Icon name="image" size={15} />图示
    </button>
    <div ref={panel} id={id} popover="manual" role="dialog" aria-label={`${title}图示`} className="guide-illustration-panel">
      <div className="guide-illustration-header"><strong>{title}图示</strong><button type="button" aria-label="关闭图示" onClick={() => dismiss(true)}><Icon name="close" size={17} /></button></div>
      {caption && <p className="guide-illustration-caption">{caption}</p>}
      <div className="guide-illustration-image">
        {failed ? <p>图片暂时无法显示，请刷新页面重试。</p> : <img src={src} alt={alt} onError={() => setFailed(true)} />}
      </div>
      <a className="guide-illustration-original" href={src} target="_blank" rel="noreferrer">查看大图<Icon name="external" size={14} /></a>
    </div>
  </div>;
}
