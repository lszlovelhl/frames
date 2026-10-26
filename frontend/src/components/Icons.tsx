/* 帧间图标集 —— SF Symbols 风格线性图标（stroke=currentColor） */
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;

function base(props: P, children: React.ReactNode) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {children}
    </svg>
  );
}

export const IconMessage = (p: P) => base(p, (
  <><path d="M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9l-5 4V6a1 1 0 0 1 1-1z" /><path d="M8 10h8M8 13.5h5" /></>
));
export const IconSparkles = (p: P) => base(p, (
  <><path d="M12 3l1.8 4.8L18.5 9.5l-4.7 1.7L12 16l-1.8-4.8L5.5 9.5l4.7-1.7z" /><path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" /></>
));
export const IconPlay = (p: P) => base(p, (
  <><path d="M6 4.5v15l13-7.5z" /></>
));
export const IconLibrary = (p: P) => base(p, (
  <><path d="M4 4h7v16H4zM13 4h7v10h-7z" /><path d="M13 17h7" /></>
));
export const IconCreate = (p: P) => base(p, (
  <><path d="M12 5v14M5 12h14" /></>
));
export const IconElements = (p: P) => base(p, (
  <><rect x="4" y="4" width="7" height="7" rx="2" /><rect x="13" y="4" width="7" height="7" rx="2" /><rect x="4" y="13" width="7" height="7" rx="2" /><path d="M13 13h7v7h-7z" /></>
));
export const IconProducts = (p: P) => base(p, (
  <><path d="M12 3l8 4v10l-8 4-8-4V7z" /><path d="M12 11l8-4M12 11v10M12 11L4 7" /></>
));
export const IconCollection = (p: P) => base(p, (
  <><path d="M20 7l-8-4-8 4v10l8 4 8-4z" /><path d="M4 7l8 4 8-4M12 11v10" /></>
));
export const IconPrompt = (p: P) => base(p, (
  <><path d="M5 4h14a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9l-5 4z" /><path d="M9 9h6M9 12h4" /></>
));
export const IconUsage = (p: P) => base(p, (
  <><path d="M3 12a9 9 0 1 0 9-9" /><path d="M12 3v9l6 3" /></>
));
export const IconModel = (p: P) => base(p, (
  <><rect x="6" y="6" width="12" height="12" rx="3" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></>
));
export const IconAccounts = (p: P) => base(p, (
  <><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></>
));
export const IconSettings = (p: P) => base(p, (
  <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" /></>
));
export const IconSearch = (p: P) => base(p, (
  <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></>
));
export const IconSend = (p: P) => base(p, (
  <><path d="M21 3L10.5 13.5M21 3l-7 18-3.5-7.5L3 10z" /></>
));
export const IconFork = (p: P) => base(p, (
  <><circle cx="6" cy="5" r="2.5" /><circle cx="18" cy="19" r="2.5" /><circle cx="6" cy="19" r="2.5" /><path d="M6 7.5v9M8.4 19H15.5M6 10c4 0 5 2 9 6" /></>
));
export const IconTrash = (p: P) => base(p, (
  <><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></>
));
export const IconCopy = (p: P) => base(p, (
  <><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>
));
export const IconCheck = (p: P) => base(p, (<path d="M4 12.5l5 5L20 6.5" />));
export const IconChevron = (p: P) => base(p, (<path d="M9 6l6 6-6 6" />));
export const IconChevronLeft = (p: P) => base(p, (<path d="M15 6l-6 6 6 6" />));
export const IconLogout = (p: P) => base(p, (
  <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5M21 12H9" /></>
));
export const IconMemory = (p: P) => base(p, (
  <><rect x="6" y="3" width="12" height="18" rx="3" /><path d="M6 8h12M6 16h12M9 8v8M12 8v8M15 8v8" /></>
));
export const IconEdit = (p: P) => base(p, (
  <><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z" /></>
));
export const IconClose = (p: P) => base(p, (<path d="M6 6l12 12M18 6L6 18" />));
export const IconRefresh = (p: P) => base(p, (
  <><path d="M21 12a9 9 0 1 1-2.6-6.3M21 3v6h-6" /></>
));
export const IconExternal = (p: P) => base(p, (
  <><path d="M14 4h6v6M20 4L10 14" /><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6" /></>
));
export const IconVideo = (p: P) => base(p, (
  <><rect x="3" y="6" width="12" height="12" rx="2" /><path d="M15 10l6-3v10l-6-3z" /></>
));
export const IconDoc = (p: P) => base(p, (
  <><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4M10 12h5M10 16h5" /></>
));
export const IconBrain = (p: P) => base(p, (
  <><path d="M12 3a4 4 0 0 0-3.6 2.3A3.8 3.8 0 0 0 4 9a3.7 3.7 0 0 0 1.4 5A4 4 0 0 0 12 18z" /><path d="M12 3a4 4 0 0 1 3.6 2.3A3.8 3.8 0 0 1 20 9a3.7 3.7 0 0 1-1.4 5A4 4 0 0 1 12 18z" /><path d="M12 18v3M9 21h6" /></>
));
export const IconStar = (p: P) => base(p, (
  <path d="M12 3l2.7 5.6 6.1.8-4.5 4.2 1.2 6-5.5-3-5.5 3 1.2-6L3.2 9.4l6.1-.8z" />
));
export const IconHeart = (p: P) => base(p, (
  <path d="M12 20.5C7 16 4 13 4 9.5A4.5 4.5 0 0 1 12 6a4.5 4.5 0 0 1 8 3.5c0 3.5-3 6.5-8 11z" />
));
export const IconLink = (p: P) => base(p, (
  <><path d="M10 14a5 5 0 0 0 7.1.1l3-3a5 5 0 0 0-7-7l-1.5 1.5" /><path d="M14 10a5 5 0 0 0-7.1-.1l-3 3a5 5 0 0 0 7 7l1.5-1.5" /></>
));
export const IconHome = (p: P) => base(p, (
  <><path d="M4 11l8-7 8 7v9a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1z" /></>
));
export const IconMore = (p: P) => base(p, (
  <><circle cx="5" cy="12" r="1" fill="currentColor" /><circle cx="12" cy="12" r="1" fill="currentColor" /><circle cx="19" cy="12" r="1" fill="currentColor" /></>
));
