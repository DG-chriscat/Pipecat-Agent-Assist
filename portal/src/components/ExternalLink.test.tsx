import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExternalLink } from "./ExternalLink";

describe("ExternalLink", () => {
  it("opens in a new tab without giving the new page access to this one", () => {
    render(<ExternalLink href="https://docs.pipecat.ai/overview/introduction">Pipecat</ExternalLink>);
    const a = screen.getByRole("link", { name: /pipecat/i });
    expect(a).toHaveAttribute("href", "https://docs.pipecat.ai/overview/introduction");
    expect(a).toHaveAttribute("target", "_blank");
    expect(a).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("tells screen readers it opens a new tab", () => {
    render(<ExternalLink href="https://docs.fly.io">Fly</ExternalLink>);
    expect(screen.getByRole("link", { name: "Fly (opens in a new tab)" })).toBeInTheDocument();
  });
});
