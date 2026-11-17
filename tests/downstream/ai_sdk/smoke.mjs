// Drive a FastMCP server through the Vercel AI SDK's MCP client (`@ai-sdk/mcp`).
//
// The AI SDK has its own TypeScript MCP client, for both protocol eras, rather
// than the `mcp` Python SDK the other smokes share with FastMCP, so it checks
// FastMCP's wire format against an implementation that was written separately.
//
// smoke_ai_sdk.py starts the servers and runs this once per connection with a
// JSON config argument; it prints one JSON line per check for the harness.

import { createMCPClient, ElicitationRequestSchema } from "@ai-sdk/mcp";
import { Experimental_StdioMCPTransport } from "@ai-sdk/mcp/mcp-stdio";
import { generateText, stepCountIs } from "ai";
import { MockLanguageModelV4 } from "ai/test";

const config = JSON.parse(process.argv[2]);
const gaps = config.gaps ?? {};
const modern = config.era === "modern";
const MODERN_VERSION = "2026-07-28";

function assert(condition, message) {
  if (!condition) throw new Error(`assertion failed: ${JSON.stringify(message)}`);
}

function emit(check, error, ms, gap) {
  console.log(JSON.stringify({ check, error, ms, gap }));
}

async function check(name, fn) {
  const start = performance.now();
  let error = null;
  try {
    await fn();
  } catch (e) {
    error = `${e?.name ?? "Error"}: ${e?.message ?? e}`;
  }
  emit(name, error, performance.now() - start, gaps[name] ?? null);
}

function transport(headers = config.headers) {
  if (config.transport === "stdio") {
    const stdio = new Experimental_StdioMCPTransport({
      command: config.command,
      args: config.args,
      env: config.env,
      stderr: "ignore",
    });
    // Over stdio the SDK gives `server/discover` 1s before it falls back to
    // `initialize`. A Python server takes longer than that to start, answers
    // the discover, and then refuses the late `initialize` on a connection
    // that is already serving 2026-07-28. Wait for the discover the way the
    // SDK's HTTP transport does, so the modern era is what gets checked.
    stdio.waitsForProtocolVersionDiscovery = true;
    return stdio;
  }
  return { type: config.transport, url: config.url, headers };
}

async function connect(headers) {
  const client = await createMCPClient({
    transport: transport(headers),
    // The modern era is discovered with `server/discover`; turning discovery
    // off sends a handshake-era `initialize` first.
    protocolVersionDiscovery: modern,
    capabilities: { elicitation: {} },
    clientName: "downstream-smoke",
  });
  client.onElicitationRequest(ElicitationRequestSchema, async (request) => {
    assert(String(request.params.message).startsWith("approve"), request);
    return { action: "accept", content: { approved: true } };
  });
  return client;
}

// A model that calls add and forecast on its first step, then answers with
// what they returned, as the AI SDK handed the results back to it.
function scriptedModel() {
  const usage = {
    inputTokens: { total: 1, noCache: 1, cacheRead: 0, cacheWrite: 0 },
    outputTokens: { total: 1, text: 1, reasoning: 0 },
  };
  return new MockLanguageModelV4({
    doGenerate: async ({ prompt }) => {
      const results = prompt
        .filter((m) => m.role === "tool")
        .flatMap((m) => m.content)
        .filter((part) => part.type === "tool-result")
        .map((part) => part.output.value.find((b) => b.type === "text").text);
      if (results.length) {
        return {
          content: [{ type: "text", text: JSON.stringify(results) }],
          finishReason: { unified: "stop", raw: "stop" },
          usage,
          warnings: [],
        };
      }
      return {
        content: [
          { type: "tool-call", toolCallId: "call-add", toolName: "add", input: '{"a": 2, "b": 3}' },
          {
            type: "tool-call",
            toolCallId: "call-forecast",
            toolName: "forecast",
            input: '{"city": "Chicago", "days": 2}',
          },
        ],
        finishReason: { unified: "tool-calls", raw: "tool_calls" },
        usage,
        warnings: [],
      };
    },
  });
}

const textOf = (result) => result.content.find((b) => b.type === "text")?.text;

async function main() {
  if (config.headers) {
    await check("rejects missing bearer token", async () => {
      let client;
      try {
        client = await connect({});
        await client.listTools();
      } catch {
        return;
      } finally {
        await client?.close().catch(() => {});
      }
      throw new Error("listed tools without a bearer token");
    });
  }

  let client;
  try {
    client = await connect();
  } catch (e) {
    emit("connect", `${e?.name ?? "Error"}: ${e?.message ?? e}`, 0, gaps.connect ?? null);
    return;
  }

  try {
    await check("negotiated era", async () => {
      const version = client.initializeResult.protocolVersion;
      assert(modern ? version === MODERN_VERSION : version !== MODERN_VERSION, version);
    });

    await check("server info", async () => {
      assert(client.serverInfo.name === "downstream-smoke", client.serverInfo);
      assert(client.instructions === config.instructions, client.instructions);
    });

    await check("lists tools", async () => {
      const names = new Set((await client.listTools()).tools.map((t) => t.name));
      const missing = config.tools.filter((t) => !names.has(t));
      assert(missing.length === 0, missing);
    });

    await check("agent loop", async () => {
      const { text } = await generateText({
        model: scriptedModel(),
        tools: await client.tools(),
        stopWhen: stepCountIs(3),
        prompt: "add 2 and 3, then get the Chicago forecast",
      });
      const [sum, forecast] = JSON.parse(text);
      assert(sum === "5", text);
      assert(JSON.parse(forecast).conditions.join() === "sunny,sunny", text);
    });

    await check("structured output", async () => {
      const result = await client.callTool({ name: "forecast", arguments: { city: "Oslo" } });
      const expected = { city: "Oslo", celsius: 21.5, conditions: ["sunny"] };
      assert(JSON.stringify(result.structuredContent) === JSON.stringify(expected), result);
    });

    await check("tool error", async () => {
      let result;
      try {
        result = await client.callTool({ name: "divide", arguments: { a: 1, b: 0 } });
      } catch (e) {
        assert(String(e?.message).includes("divide by zero"), String(e));
        return;
      }
      assert(result.isError && textOf(result)?.includes("divide by zero"), result);
    });

    await check("image", async () => {
      const [block] = (await client.callTool({ name: "snapshot" })).content;
      assert(block.type === "image" && block.mimeType === "image/png" && block.data, block);
    });

    await check("audio", async () => {
      const [block] = (await client.callTool({ name: "chime" })).content;
      assert(block.type === "audio" && block.mimeType === "audio/wav" && block.data, block);
    });

    await check("mixed content", async () => {
      const blocks = (await client.callTool({ name: "attachments" })).content;
      assert(
        JSON.stringify(blocks.map((b) => b.type)) ===
          JSON.stringify(["text", "resource", "resource_link"]),
        blocks,
      );
      assert(blocks[0].text === "see attached" && blocks[1].resource.text === "buy milk", blocks);
      assert(blocks[2].uri === "config://app", blocks[2]);
    });

    await check("float count output schema", async () => {
      const result = await client.callTool({ name: "stamp" });
      assert(result.structuredContent?.when === "2026-09-22T00:00:00Z", result);
    });

    await check("progress and logging tool", async () => {
      const result = await client.callTool({ name: "count_to", arguments: { n: 3 } });
      assert(textOf(result) === "counted to 3", result);
    });

    await check("elicitation", async () => {
      const result = await client.callTool({ name: "confirm", arguments: { action: "deploy" } });
      assert(textOf(result) === "deploy: approved", result);
    });

    await check("prompt", async () => {
      const listed = await client.listPrompts();
      assert(listed.prompts.some((p) => p.name === "review"), listed);
      const result = await client.getPrompt({ name: "review", arguments: { code: "x = 1" } });
      assert(JSON.stringify(result.messages[0].content).includes("x = 1"), result);
    });

    await check("resources", async () => {
      const listed = await client.listResources();
      assert(listed.resources.some((r) => r.uri === "config://app"), listed);
      const [content] = (await client.readResource({ uri: "config://app" })).contents;
      assert(JSON.parse(content.text).mode === "smoke", content);
    });

    await check("resource templates", async () => {
      const listed = await client.listResourceTemplates();
      assert(
        listed.resourceTemplates.some((t) => t.uriTemplate === "greeting://{name}"),
        listed,
      );
      const [content] = (await client.readResource({ uri: "greeting://nate" })).contents;
      assert(content.text === "hello, nate", content);
    });

    await check("template literals and list queries", async () => {
      const texts = [];
      for (const uri of Object.keys(config.template_reads)) {
        texts.push((await client.readResource({ uri })).contents[0].text);
      }
      assert(
        JSON.stringify(texts) === JSON.stringify(Object.values(config.template_reads)),
        texts,
      );
    });

    await check("call after timeout", async () => {
      let timedOut = false;
      try {
        await client.callTool({
          name: "sleep",
          arguments: { seconds: 5 },
          options: { timeout: 1000 },
        });
      } catch {
        timedOut = true;
      }
      assert(timedOut, "a 5s call finished inside 1s");
      const result = await client.callTool({ name: "add", arguments: { a: 1, b: 1 } });
      assert(result.structuredContent?.result === 2, result);
    });
  } finally {
    await client.close().catch(() => {});
  }
}

await main();
// The stdio transport and HTTP keep-alive sockets can hold the loop open.
process.exit(0);
