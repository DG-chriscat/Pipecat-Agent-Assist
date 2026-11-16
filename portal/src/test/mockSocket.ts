/** Minimal WebSocket stand-in for tests. Drive it with server* helpers. */
export class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  static reset(): void {
    MockWebSocket.instances = [];
  }

  static last(): MockWebSocket {
    const s = MockWebSocket.instances.at(-1);
    if (!s) throw new Error("no MockWebSocket created");
    return s;
  }

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  sent: string[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    if (this.readyState !== MockWebSocket.OPEN) throw new Error("socket not open");
    this.sent.push(data);
  }

  close(code = 1000): void {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }

  serverOpen(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  serverSend(msg: unknown): void {
    this.serverSendRaw(JSON.stringify(msg));
  }

  serverSendRaw(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
  }

  serverClose(code: number): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }
}
