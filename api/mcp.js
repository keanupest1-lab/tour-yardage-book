// Course Data connector: remote MCP server (Streamable HTTP, stateless) as a Vercel function.
// Add to Claude as a custom connector: https://<your-app>.vercel.app/api/mcp?key=<ACCESS_KEY>
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { registerTools } from '../server/tools.js';

export const config = { maxDuration: 60 };

export default async function handler(req, res) {
  const key = new URL(req.url, 'http://x').searchParams.get('key');
  if (process.env.ACCESS_KEY && key !== process.env.ACCESS_KEY) {
    res.statusCode = 401; res.setHeader('Content-Type', 'application/json');
    return res.end(JSON.stringify({ error: 'Missing or wrong access key' }));
  }
  if (req.method !== 'POST') {
    res.statusCode = 405; res.setHeader('Allow', 'POST');
    return res.end(JSON.stringify({ jsonrpc: '2.0', error: { code: -32000, message: 'Method not allowed' }, id: null }));
  }
  const server = new McpServer({ name: 'course-data', version: '0.2.0' });
  registerTools(server, { MAPBOX_TOKEN: process.env.MAPBOX_TOKEN });
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
  res.on('close', () => { transport.close(); server.close(); });
  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
}
