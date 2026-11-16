import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  SettingsInvalid,
  disclosure,
  type LlmOption,
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
  tts_speed: 1.0,
  tts_expressivity: 0,
  slow_readback: true,
  readback_speed: 0.9,
  llm_intake: "phonellm-mini-alpha-2-fp8",
  llm_assist: "claude-haiku-5-5",
  llm_wrapup: "claude-haiku-5-5",
};

const LLM_MODELS: LlmOption[] = [
  { id: "claude-haiku-5-5", name: "Claude Haiku", provider: "anthropic", early_access: false, note: null },
  { id: "phonellm-mini-alpha-2-fp8", name: "Deepgram phoneLLM mini", provider: "deepgram", early_access: true, note: null },
  {
    id: "think-fast",
    name: "Deepgram Think-Fast",
    provider: "deepgram",
    early_access: true,
    note: "Always stored for Deepgram model training (no opt out)",
  },
];

const CONTROLS = { tts_speed: 1.0, tts_expressivity: 0, slow_readback: true, readback_speed: 0.9 };

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
    limits: {
      greeting_text: 400,
      hold_update_text: 300,
      hold_update_secs: [5, 600],
      tts_speed: [0.8, 1.2],
      readback_speed: [0.8, 1.0],
      speed_step: 0.05,
    },
    stt_models: {
      intake: ["nova-3-general", "flux-general-en"],
      assist: ["nova-3-general", "nova-3-medical", "flux-general-en"],
    },
    llm_models: LLM_MODELS,
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
    expect(screen.getByRole("switch", { name: /identity read back/i })).toHaveAttribute("aria-checked", "true");
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
    fireEvent.click(screen.getByRole("switch", { name: /identity read back/i }));
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
    expect(api.preview).toHaveBeenCalledWith("flux", "flux-hannah-en", undefined, CONTROLS);
    expect(play).toHaveBeenCalledTimes(1);
  });

  it("Preview greeting reads the greeting being edited", async () => {
    const { api } = await setup();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /hear the greeting/i })));
    expect(api.preview).toHaveBeenCalledWith("aura-2", "aura-2-thalia-en", DEFAULTS.greeting_text, CONTROLS);
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

describe("Flux voice controls (P5-8, AP-22)", () => {
  const flux = () => fakeApi({ get: vi.fn(async () => response({ tts_model: "flux", tts_voice: "flux-alexis-en" })) });
  const speed = () => screen.getByRole("slider", { name: /^speed$/i }) as HTMLInputElement;
  const expressiveness = () => screen.getByRole("radiogroup", { name: /expressiveness/i });
  const slowSwitch = () => screen.getByRole("switch", { name: /slow down for read backs/i });

  it("shows speed, expressiveness, and slow read backs in the Voice section", async () => {
    await setup(flux());
    expect(speed()).toHaveAttribute("min", "0.8");
    expect(speed()).toHaveAttribute("max", "1.2");
    expect(speed()).toHaveAttribute("step", "0.05");
    expect(speed().value).toBe("1");
    expect(screen.getByText("1.0x")).toBeInTheDocument();
    expect(screen.getByText(/normal/i)).toBeInTheDocument();
    expect(within(expressiveness()).getAllByRole("radio").map((r) => r.textContent)).toEqual([
      "Calm",
      "Steady",
      "Natural",
      "Warm",
      "Animated",
    ]);
    expect(within(expressiveness()).getByRole("radio", { name: "Natural" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/^beta$/i)).toBeInTheDocument();
    expect(slowSwitch()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/member ids, dates of birth, and claim numbers/i)).toBeInTheDocument();
  });

  it("saves the picked speed, expressiveness, and toggle", async () => {
    const api = flux();
    await setup(api);
    fireEvent.change(speed(), { target: { value: "1.1" } });
    expect(screen.getByText("1.1x")).toBeInTheDocument();
    fireEvent.click(within(expressiveness()).getByRole("radio", { name: "Warm" }));
    fireEvent.click(slowSwitch());
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^save$/i })));
    expect(api.save).toHaveBeenCalledWith(
      {
        ...DEFAULTS,
        tts_model: "flux",
        tts_voice: "flux-alexis-en",
        tts_speed: 1.1,
        tts_expressivity: 1,
        slow_readback: false,
      },
      "Ana Ruiz",
    );
  });

  it("are disabled with a note when Aura-2 is selected", async () => {
    await setup();
    expect(speed()).toBeDisabled();
    for (const r of within(expressiveness()).getAllByRole("radio")) expect(r).toBeDisabled();
    expect(slowSwitch()).toBeDisabled();
    expect(screen.getByText(/only for flux voices/i)).toBeInTheDocument();
    fireEvent.click(ttsFlux());
    expect(speed()).not.toBeDisabled();
    expect(slowSwitch()).not.toBeDisabled();
    expect(screen.queryByText(/only for flux voices/i)).not.toBeInTheDocument();
  });

  it("Preview sends the chosen speed, expressiveness, and read back setting", async () => {
    const api = flux();
    const { play } = await setup(api);
    fireEvent.change(speed(), { target: { value: "1.1" } });
    fireEvent.click(within(expressiveness()).getByRole("radio", { name: "Warm" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^preview$/i })));
    expect(api.preview).toHaveBeenCalledWith("flux", "flux-alexis-en", undefined, {
      tts_speed: 1.1,
      tts_expressivity: 1,
      slow_readback: true,
      readback_speed: 0.9,
    });
    expect(play).toHaveBeenCalledTimes(1);
  });

  it("fills the Flux control defaults when the server sends none", async () => {
    const { tts_speed, tts_expressivity, slow_readback, readback_speed, ...older } = DEFAULTS;
    void [tts_speed, tts_expressivity, slow_readback, readback_speed];
    const api = fakeApi({ get: vi.fn(async () => ({ ...response(), values: older as SettingsValues })) });
    await setup(api);
    expect(screen.getByText("1.0x")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("uses the slider range the server sends", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        const r = response({ tts_model: "flux", tts_voice: "flux-alexis-en" });
        return { ...r, limits: { ...r.limits, tts_speed: [0.9, 1.1] as [number, number] } };
      }),
    });
    await setup(api);
    expect(speed()).toHaveAttribute("min", "0.9");
    expect(speed()).toHaveAttribute("max", "1.1");
  });
});

describe("disclosure (IN-2)", () => {
  it("needs both the AI and the person parts", () => {
    expect(disclosure(DEFAULTS.greeting_text)).toEqual({ ai: true, person: true });
    expect(disclosure("Hello there.")).toEqual({ ai: false, person: false });
    expect(disclosure("I said a person will join.")).toEqual({ ai: false, person: true });
  });
});

describe("Settings Thinking group: LLM per job (P5-9, DC-11)", () => {
  const job = (name: RegExp) => screen.getByRole("radiogroup", { name });
  const picked = (group: HTMLElement) =>
    within(group)
      .getAllByRole("radio")
      .filter((r) => r.getAttribute("aria-checked") === "true")
      .map((r) => r.getAttribute("data-model"));

  it("has a picker per job with every LLM and shows the saved picks", async () => {
    await setup();
    const thinking = screen.getByRole("group", { name: /thinking/i });
    expect(within(thinking).getByText(/llm for each job/i)).toBeInTheDocument();
    const want: [RegExp, string][] = [
      [/^intake llm$/i, "phonellm-mini-alpha-2-fp8"],
      [/^assist and ask llm$/i, "claude-haiku-5-5"],
      [/^wrap up llm$/i, "claude-haiku-5-5"],
    ];
    for (const [name, model] of want) {
      const group = job(name);
      expect(within(group).getAllByRole("radio").map((r) => r.getAttribute("data-model"))).toEqual([
        "claude-haiku-5-5",
        "phonellm-mini-alpha-2-fp8",
        "think-fast",
      ]);
      expect(picked(group)).toEqual([model]);
    }
  });

  it("marks each Deepgram LLM Early Access and Claude not", async () => {
    await setup();
    const group = job(/^intake llm$/i);
    const radio = (id: string) => within(group).getAllByRole("radio").find((r) => r.getAttribute("data-model") === id)!;
    expect(within(radio("claude-haiku-5-5")).queryByText("Early Access")).toBeNull();
    expect(within(radio("phonellm-mini-alpha-2-fp8")).getByText("Early Access")).toBeInTheDocument();
    expect(within(radio("think-fast")).getByText("Early Access")).toBeInTheDocument();
    expect(radio("phonellm-mini-alpha-2-fp8")).toHaveTextContent(/phoneLLM mini/);
    expect(radio("think-fast")).toHaveTextContent(/Think-Fast/);
  });

  it("shows the Think-Fast training note in the picker", async () => {
    await setup();
    const notes = screen.getAllByText(/always stored for deepgram model training \(no opt out\)/i);
    expect(notes.length).toBe(3);
  });

  it("flags the note when Think-Fast is picked", async () => {
    await setup();
    const group = job(/^wrap up llm$/i);
    const think = within(group).getAllByRole("radio").find((r) => r.getAttribute("data-model") === "think-fast")!;
    fireEvent.click(think);
    const row = group.closest(".settings-row") as HTMLElement;
    expect(within(row).getByText(/always stored/i).closest("p")).toHaveClass("is-warn");
  });

  it("saves the picked LLM for each job", async () => {
    const { api } = await setup();
    const pick = (name: RegExp, id: string) =>
      fireEvent.click(within(job(name)).getAllByRole("radio").find((r) => r.getAttribute("data-model") === id)!);
    pick(/^intake llm$/i, "claude-haiku-5-5");
    pick(/^assist and ask llm$/i, "think-fast");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^save$/i })));
    expect(api.save).toHaveBeenCalledWith(
      { ...DEFAULTS, llm_intake: "claude-haiku-5-5", llm_assist: "think-fast" },
      "Ana Ruiz",
    );
  });

  it("links the Deepgram LLM docs in a new tab", async () => {
    await setup();
    const link = screen.getByRole("link", { name: /deepgram llm docs/i });
    expect(link).toHaveAttribute("href", "https://developers.deepgram.com/docs/llm");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("hides the group when the server lists no LLMs", async () => {
    const api = fakeApi({
      get: vi.fn(async (): Promise<SettingsResponse> => ({ ...response(), llm_models: undefined })),
    });
    await setup(api);
    expect(screen.queryByRole("group", { name: /thinking/i })).toBeNull();
  });
});
