import type { ReactNode } from "react";

/**
 * Every external link in the portal (AP-21): opens in a new tab, and the new page gets
 * no handle on this one. Architecture.test.tsx fails if any http link skips this.
 */
export function ExternalLink({ href, className, children }: { href: string; className?: string; children: ReactNode }) {
  return (
    <a href={href} className={className} target="_blank" rel="noopener noreferrer">
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}
