import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  SettingsInvalid,
  disclosure,
  type SettingsApi,
  type SettingsResponse,
  type SettingsValues,
} from "../api/settingsClient";
import { Settings } from "./Settings";

const DEFAULTS: SettingsValues = {
  tts_model: "aura-2",
  tts_voice: "aura-2-thalia-en",
  greeting_text:
    "Hi, thanks for calling. You're speaking with Deepgram Vitals, an AI assistant. I'll ask a few quick questions, and then a person will join the call to help you.",
  intake_pack: "claims_status",
  identity_read_back: true,
  hold_update_text: "Thanks for waiting. {place} A person will be with you shortly.",
  hold_update_secs: 60,
  stt_intake_model: "nova-3-general",
  stt_assist_model: "nova-3-general",
};

function response(values: Partial<SettingsValues> = {}, isDefault = true): SettingsResponse {
  return {
    values: { ...DEFAULTS, ...values },
    defaults: DEFAULTS,
    is_default: isDefault,
    updated_at: isDefault ? null : "2026-11-16T12:00:00+00:00",
    packs: [
      {
        id: "claims_status",
        name: "Claim status",
        questions: [
          { key: "claim_number", ask: "Do you have the claim number?" },
          { key: "service_date", ask: "About when was the service?" },
        ],
      },
      { id: "account_payments", name: "Account and payments", questions: [{ key: "x", ask: "Which bill?" }] },
    ],
    limits: { greeting_text: 400, hold_update_text: 300, hold_update_secs: [5, 600] },
    stt_models: {
      intake: ["nova-3-general", "flux-general-en"],
      assist: ["nova-3-general", "nova-3-medical", "flux-general-en"],
    },
  };
}

function fakeApi(over: Partial<SettingsApi> = {}): SettingsApi {
  return {
    get: vi.fn(async () => response()),
    save: vi.fn(async (values: SettingsValues) => response(values, false)),
    reset: vi.fn(async () => response()),
    voices: vi.fn(async () => ({
      "aura-2": [
        { id: "aura-2-thalia-en", name: "Thalia", accent: "American", gender: "Female" },
        { id: "aura-2-orion-en", name: "Orion", accent: "American", gender: "Male" },
      ],
      flux: [
        { id: "flux-hannah-en", name: "Hannah", accent: "American", gender: "Female" },
        { id: "flux-alexis-en", name: "Alexis", accent: "American", gender: "Female" },
      ],
    })),
    preview: vi.fn(async () => new Blob(["ID3"], { type: "audio/mpeg" })),
    ...over,
  };
}

async function setup(api = fakeApi(), play = vi.fn(async () => {})) {
  render(<Settings api={api} agentName="Ana Ruiz" play={play} />);
  await screen.findByLabelText(/^voice$/i);
  return { api, play };
}

/** The TTS model's Flux (the Listening group has STT Flux radios too). */
const ttsFlux = () =>
  within(screen.getByRole("radiogroup", { name: /^model$/i })).getByRole("radio", { name: "Flux" });

describe("Settings page (P5-5, AP-20)", () => {
  it("loads the saved settings and says changes apply to the next call", async () => {
    await setup();
    expect(screen.getByRole("radio", { name: "Aura-2" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText(/^voice$/i)).toHaveValue("aura-2-thalia-en");
    expect(screen.getByLabelText(/greeting/i)).toHaveValue(DEFAULTS.greeting_text);
    expect(screen.getByRole("switch", { name: /read back/i })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText(/hold message/i)).toHaveValue(DEFAULTS.hold_update_text);
    expect(screen.getByLabelText(/every/i)).toHaveValue(60);
    expect(screen.getByText(/applies to the next call/i)).toBeInTheDocument();
  });

  it("shows the pack's questions read only", async () => {
    await setup();
    const list = screen.getByRole("list", { name: /intake questions/i });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Do you have the claim number?",
      "About when was the service?",
    ]);
    fireEvent.click(screen.getByRole("radio", { name: "Account and payments" }));
    expect(within(screen.getByRole("list", { name: /intake questions/i })).getByText("Which bill?")).toBeInTheDocument();
  });

  it("switching to Flux lists Flux voices and picks the first", async () => {
    await setup();
    fireEvent.click(ttsFlux());
    const voice = screen.getByLabelText(/^voice$/i);
    expect(voice).toHaveValue("flux-hannah-en");
    expect(within(voice).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
      "flux-hannah-en",
      "flux-alexis-en",
    ]);
  });

  it("saves the edited values with the agent's name", async () => {
    const { api } = await setup();
    const save = screen.getByRole("button", { name: /^save$/i });
    expect(save).toBeDisabled();
    fireEvent.click(ttsFlux());
    fireEvent.change(screen.getByLabelText(/^voice$/i), { target: { value: "flux-alexis-en" } });
    fireEvent.click(screen.getByRole("switch", { name: /read back/i }));
    fireEvent.change(screen.getByLabelText(/every/i), { target: { value: "30" } });
    expect(save).toBeEnabled();
    await act(async () => fireEvent.click(save));
    expect(api.save).toHaveBeenCalledWith(
      { ...DEFAULTS, tts_model: "flux", tts_voice: "flux-alexis-en", identity_read_back: false, hold_update_secs: 30 },
      "Ana Ruiz",
    );
    expect(await screen.findByRole("status")).toHaveTextContent(/saved\. applies to the next call/i);
    expect(save).toBeDisabled();
  });

  it("checks the AI disclosure as you type and blocks Save without it", async () => {
    await setup();
    const greeting = screen.getByLabelText(/greeting/i);
    fireEvent.change(greeting, { target: { value: "Hello, thanks for calling." } });
    const checks = screen.getByRole("list", { name: /disclosure/i });
    expect(within(checks).getByText(/AI assistant/).closest("li")).toHaveClass("is-missing");
    expect(within(checks).getByText(/person will join/).closest("li")).toHaveClass("is-missing");
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
    fireEvent.change(greeting, {
      target: { value: "Hello, you're talking to an AI assistant. A person will join after a few questions." },
    });
    expect(within(checks).getByText(/AI assistant/).closest("li")).toHaveClass("is-ok");
    expect(screen.getByRole("button", { name: /^save$/i })).toBeEnabled();
  });

  it("shows the server's field errors when it refuses a save", async () => {
    const api = fakeApi({
      save: vi.fn(async () => {
        throw new SettingsInvalid({ tts_voice: "Pick a flux voice from the list." });
      }),
    });
    await setup(api);
    fireEvent.change(screen.getByLabelText(/every/i), { target: { value: "45" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^save$/i })));
    expect(await screen.findByText("Pick a flux voice from the list.")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/need a fix/i);
  });

  it("checks the hold interval range before saving", async () => {
    await setup();
    fireEvent.change(screen.getByLabelText(/every/i), { target: { value: "2" } });
    expect(screen.getByText(/5 to 600 seconds/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("Reset to defaults asks the server and shows the defaults", async () => {
    const api = fakeApi({ get: vi.fn(async () => response({ tts_model: "flux", tts_voice: "flux-hannah-en" }, false)) });
    await setup(api);
    expect(ttsFlux()).toHaveAttribute("aria-checked", "true");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i })));
    expect(api.reset).toHaveBeenCalledWith("Ana Ruiz");
    await waitFor(() => expect(screen.getByRole("radio", { name: "Aura-2" })).toHaveAttribute("aria-checked", "true"));
    expect(screen.getByRole("status")).toHaveTextContent(/defaults/i);
  });

  it("Preview plays a sample line in the picked voice through the server", async () => {
    const { api, play } = await setup();
    fireEvent.click(ttsFlux());
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /preview/i })));
    expect(api.preview).toHaveBeenCalledWith("flux", "flux-hannah-en", undefined);
    expect(play).toHaveBeenCalledTimes(1);
  });

  it("Preview greeting reads the greeting being edited", async () => {
    const { api } = await setup();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /hear the greeting/i })));
    expect(api.preview).toHaveBeenCalledWith("aura-2", "aura-2-thalia-en", DEFAULTS.greeting_text);
  });

  it("says when the preview fails", async () => {
    const api = fakeApi({
      preview: vi.fn(async () => {
        throw new Error("The preview could not be made.");
      }),
    });
    await setup(api);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /preview/i })));
    expect(screen.getByRole("alert")).toHaveTextContent("The preview could not be made.");
  });

  it("says when settings cannot load", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw new Error("Request failed (500)");
      }),
    });
    render(<Settings api={api} agentName="Ana" play={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Request failed (500)");
  });
});

describe("Settings Listening group (P5-7, IN-14)", () => {
  const intake = () => screen.getByRole("radiogroup", { name: /intake bot/i });
  const assist = () => screen.getByRole("radiogroup", { name: /live assist/i });
  const picked = (group: HTMLElement) =>
    within(group)
      .getAllByRole("radio")
      .filter((r) => r.getAttribute("aria-checked") === "true")
      .map((r) => r.textContent);

  it("sits in the Voice section with a picker per side and a line of help each", async () => {
    await setup();
    const listening = screen.getByRole("group", { name: /listening/i });
    expect(within(listening).getByText(/speech to text/i)).toBeInTheDocument();
    expect(within(intake()).getAllByRole("radio").map((r) => r.textContent)).toEqual(["Nova-3", "Flux"]);
    expect(within(assist()).getAllByRole("radio").map((r) => r.textContent)).toEqual([
      "Nova-3",
      "Nova-3 Medical",
      "Flux",
    ]);
    expect(picked(intake())).toEqual(["Nova-3"]);
    expect(picked(assist())).toEqual(["Nova-3"]);
    expect(screen.getByText(/flux decides when the caller is done/i)).toBeInTheDocument();
    expect(screen.getByText(/transcribes the caller and the agent/i)).toBeInTheDocument();
  });

  it("saves the picked models", async () => {
    const { api } = await setup();
    fireEvent.click(within(intake()).getByRole("radio", { name: "Flux" }));
    fireEvent.click(within(assist()).getByRole("radio", { name: "Nova-3 Medical" }));
    expect(picked(intake())).toEqual(["Flux"]);
    expect(picked(assist())).toEqual(["Nova-3 Medical"]);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^save$/i })));
    expect(api.save).toHaveBeenCalledWith(
      { ...DEFAULTS, stt_intake_model: "flux-general-en", stt_assist_model: "nova-3-medical" },
      "Ana Ruiz",
    );
  });

  it("offers only the models the server lists for each side", async () => {
    const api = fakeApi({
      get: vi.fn(
        async (): Promise<SettingsResponse> => ({
          ...response(),
          stt_models: { intake: ["nova-3-general"], assist: ["nova-3-general", "flux-general-en"] },
        }),
      ),
    });
    await setup(api);
    expect(within(intake()).getAllByRole("radio").map((r) => r.textContent)).toEqual(["Nova-3"]);
    expect(within(assist()).getAllByRole("radio").map((r) => r.textContent)).toEqual(["Nova-3", "Flux"]);
  });

  it("shows the server's error on a model it refuses", async () => {
    const api = fakeApi({
      save: vi.fn(async () => {
        throw new SettingsInvalid({ stt_intake_model: "Pick one of nova-3-general, flux-general-en." });
      }),
    });
    await setup(api);
    fireEvent.click(within(intake()).getByRole("radio", { name: "Flux" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^save$/i })));
    expect(await screen.findByText(/pick one of nova-3-general/i)).toBeInTheDocument();
  });
});

describe("disclosure (IN-2)", () => {
  it("needs both the AI and the person parts", () => {
    expect(disclosure(DEFAULTS.greeting_text)).toEqual({ ai: true, person: true });
    expect(disclosure("Hello there.")).toEqual({ ai: false, person: false });
    expect(disclosure("I said a person will join.")).toEqual({ ai: false, person: true });
  });
});
