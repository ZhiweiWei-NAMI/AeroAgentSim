import React from 'react';

/* Inline SVG icon set for the console shell. All icons are 1em square,
 * inherit `currentColor`, and accept any svg props (className, aria-hidden…). */

export interface IconProps extends React.SVGProps<SVGSVGElement> {
  size?: number | string;
}

function icon(node: React.ReactNode): (props: IconProps) => JSX.Element {
  return function Icon({ size = 16, ...rest }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.4}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden={rest['aria-label'] ? undefined : true}
        {...rest}
      >
        {node}
      </svg>
    );
  };
}

export const HomeIcon = icon(<><path d="M2.5 6.5 8 2l5.5 4.5" /><path d="M3.5 6.8V13a.8.8 0 0 0 .8.8h7.4a.8.8 0 0 0 .8-.8V6.8" /><path d="M6.3 13.8V9.6h3.4v4.2" /></>);

export const StudioIcon = icon(<><rect x="2" y="2.5" width="12" height="11" rx="1.2" /><path d="M2 6h12" /><path d="M5.5 9.5h2.5" /><path d="M5.5 11.2h4" /></>);

export const GraphIcon = icon(<><circle cx="4" cy="4" r="1.8" /><circle cx="12" cy="6" r="1.8" /><circle cx="7" cy="12.5" r="1.8" /><path d="M5.6 4.9 10.3 5.6" /><path d="M11 7.7 8.2 11" /><path d="M5 5.7l1.3 5" /></>);

export const RunsIcon = icon(<><path d="M2.5 8a5.5 5.5 0 1 1 1.6 3.9" /><path d="M2.5 12.5V9.6h2.9" /><path d="M8 5.2V8l2.1 1.5" /></>);

export const InspectIcon = icon(<><circle cx="7" cy="7" r="4.4" /><path d="M10.3 10.3 13.8 13.8" /><path d="M5.3 7h3.4" /></>);

export const HelpIcon = icon(<><circle cx="8" cy="8" r="6" /><path d="M6.2 6.2a1.9 1.9 0 0 1 3.7.6c0 1.2-1.9 1.5-1.9 2.7" /><circle cx="8" cy="11.6" r="0.4" fill="currentColor" stroke="none" /></>);

export const CheckIcon = icon(<path d="M2.8 8.4 6.2 11.8 13.2 4.6" />);

export const ExternalIcon = icon(<><path d="M6.5 3.5H3.8a1.3 1.3 0 0 0-1.3 1.3v7.4a1.3 1.3 0 0 0 1.3 1.3h7.4a1.3 1.3 0 0 0 1.3-1.3V9.5" /><path d="M9.5 2.5h4v4" /><path d="M13.2 2.8 7.5 8.5" /></>);

export const LogoIcon = icon(<><path d="M8 2.2 13.5 5v6L8 13.8 2.5 11V5L8 2.2Z" /><path d="M8 2.2v5.4m0 0L2.5 5m5.5 2.6L13.5 5" /></>);

export const ChevronIcon = icon(<path d="M6 3.5 10.5 8 6 12.5" />);
