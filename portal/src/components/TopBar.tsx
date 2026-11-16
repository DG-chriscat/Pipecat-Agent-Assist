import type { ReactNode } from "react";
import dTile from "../assets/d-tile.png";
import wordmark from "../assets/wordmark.png";

/** Top bar: D tile, wordmark, divider, product line (DESIGN.md "Top bar"). */
export function TopBar({ center, right }: { center?: ReactNode; right?: ReactNode }) {
  return (
    <header className="topbar">
      <div className="brand">
        <img className="brand-tile" src={dTile} alt="" width={30} height={30} />
        <img className="brand-wordmark" src={wordmark} alt="Deepgram" height={19} />
        <span className="brand-sep" aria-hidden="true" />
        <span className="brand-product">Vitals</span>
      </div>
      <div className="topbar-center">{center}</div>
      <div className="topbar-right">{right}</div>
    </header>
  );
}
