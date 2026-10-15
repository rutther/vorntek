import React, { useEffect, useRef } from "react";
import IconLayoutDashboard from "@tabler/icons-react/dist/esm/icons/IconLayoutDashboard.mjs";
import IconBroadcast from "@tabler/icons-react/dist/esm/icons/IconBroadcast.mjs";
import IconAffiliate from "@tabler/icons-react/dist/esm/icons/IconAffiliate.mjs";
import IconClipboardList from "@tabler/icons-react/dist/esm/icons/IconClipboardList.mjs";
import IconForms from "@tabler/icons-react/dist/esm/icons/IconForms.mjs";
import IconChartBar from "@tabler/icons-react/dist/esm/icons/IconChartBar.mjs";
import IconBrandMeta from "@tabler/icons-react/dist/esm/icons/IconBrandMeta.mjs";
import IconWorld from "@tabler/icons-react/dist/esm/icons/IconWorld.mjs";
import IconUsers from "@tabler/icons-react/dist/esm/icons/IconUsers.mjs";
import IconCode from "@tabler/icons-react/dist/esm/icons/IconCode.mjs";
import IconDatabase from "@tabler/icons-react/dist/esm/icons/IconDatabase.mjs";
import IconCloudUpload from "@tabler/icons-react/dist/esm/icons/IconCloudUpload.mjs";
import IconFilter from "@tabler/icons-react/dist/esm/icons/IconFilter.mjs";
import IconPlus from "@tabler/icons-react/dist/esm/icons/IconPlus.mjs";
import IconArrowRight from "@tabler/icons-react/dist/esm/icons/IconArrowRight.mjs";
import IconArrowLeft from "@tabler/icons-react/dist/esm/icons/IconArrowLeft.mjs";
import IconChevronRight from "@tabler/icons-react/dist/esm/icons/IconChevronRight.mjs";
import IconChevronDown from "@tabler/icons-react/dist/esm/icons/IconChevronDown.mjs";
import IconDots from "@tabler/icons-react/dist/esm/icons/IconDots.mjs";
import IconSearch from "@tabler/icons-react/dist/esm/icons/IconSearch.mjs";
import IconPencil from "@tabler/icons-react/dist/esm/icons/IconPencil.mjs";
import IconCopy from "@tabler/icons-react/dist/esm/icons/IconCopy.mjs";
import IconArchive from "@tabler/icons-react/dist/esm/icons/IconArchive.mjs";
import IconRestore from "@tabler/icons-react/dist/esm/icons/IconRestore.mjs";
import IconX from "@tabler/icons-react/dist/esm/icons/IconX.mjs";
import IconCheck from "@tabler/icons-react/dist/esm/icons/IconCheck.mjs";
import IconInfoCircle from "@tabler/icons-react/dist/esm/icons/IconInfoCircle.mjs";
import IconAlertCircle from "@tabler/icons-react/dist/esm/icons/IconAlertCircle.mjs";
import IconDeviceFloppy from "@tabler/icons-react/dist/esm/icons/IconDeviceFloppy.mjs";
import IconMaximize from "@tabler/icons-react/dist/esm/icons/IconMaximize.mjs";
import IconAdjustments from "@tabler/icons-react/dist/esm/icons/IconAdjustments.mjs";
import IconHistory from "@tabler/icons-react/dist/esm/icons/IconHistory.mjs";
import IconTrash from "@tabler/icons-react/dist/esm/icons/IconTrash.mjs";
import IconLayoutSidebarRightCollapse from "@tabler/icons-react/dist/esm/icons/IconLayoutSidebarRightCollapse.mjs";
import IconLayoutSidebarRightExpand from "@tabler/icons-react/dist/esm/icons/IconLayoutSidebarRightExpand.mjs";
import IconMenu2 from "@tabler/icons-react/dist/esm/icons/IconMenu2.mjs";
import IconExternalLink from "@tabler/icons-react/dist/esm/icons/IconExternalLink.mjs";
import IconListNumbers from "@tabler/icons-react/dist/esm/icons/IconListNumbers.mjs";
import IconPhoto from "@tabler/icons-react/dist/esm/icons/IconPhoto.mjs";
import { STATES } from "./model.mjs";
const icons = {
  dashboard: IconLayoutDashboard,
  marketing: IconBroadcast,
  flow: IconAffiliate,
  tasks: IconClipboardList,
  form: IconForms,
  chart: IconChartBar,
  meta: IconBrandMeta,
  world: IconWorld,
  users: IconUsers,
  code: IconCode,
  database: IconDatabase,
  cloud: IconCloudUpload,
  filter: IconFilter,
  plus: IconPlus,
  right: IconArrowRight,
  left: IconArrowLeft,
  chevron: IconChevronRight,
  down: IconChevronDown,
  dots: IconDots,
  search: IconSearch,
  pencil: IconPencil,
  copy: IconCopy,
  archive: IconArchive,
  restore: IconRestore,
  close: IconX,
  check: IconCheck,
  info: IconInfoCircle,
  alert: IconAlertCircle,
  save: IconDeviceFloppy,
  fit: IconMaximize,
  settings: IconAdjustments,
  history: IconHistory,
  trash: IconTrash,
  collapse: IconLayoutSidebarRightCollapse,
  expand: IconLayoutSidebarRightExpand,
  menu: IconMenu2,
  external: IconExternalLink,
  numbers: IconListNumbers,
  image: IconPhoto,
};
export function Icon({ name, size = 19, ...props }) {
  const Component = icons[name] || IconAffiliate;
  return <Component size={size} stroke={1.7} aria-hidden="true" {...props} />;
}
export function Badge({ state, children }) {
  return (
    <span className={`badge state-${state}`}>
      <span className="status-dot" />
      {children || STATES[state] || state}
    </span>
  );
}
export function Note({ children, warning = false }) {
  return (
    <div className={`note ${warning ? "warning" : ""}`}>
      <Icon name={warning ? "alert" : "info"} size={18} />
      <div>{children}</div>
    </div>
  );
}
export function Modal({ title, children, onClose, wide = false }) {
  const ref = useRef(null);
  useEffect(() => {
    const before = document.activeElement;
    ref.current?.showModal();
    ref.current?.querySelector("input")?.focus();
    return () => {
      ref.current?.close();
      before?.focus?.();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? "wide" : ""}`}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      aria-labelledby="modal-title"
    >
      <div className="modal-header">
        <h2 id="modal-title">{title}</h2>
        <button className="icon-button" aria-label="关闭弹窗" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function Progress({ done, total }) {
  return (
    <div className="progress-cell">
      <span>
        {done} / {total} 项
      </span>
      <progress
        value={done}
        max={total}
        aria-label={`已配置 ${done} 项，共 ${total} 项`}
      />
    </div>
  );
}
export const dateText = (value) =>
  new Date(value)
    .toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    })
    .replace("/", "-");
