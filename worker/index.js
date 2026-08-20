/**
 * PriceScout — Cloudflare Worker proxy
 * Routes requests through Cloudflare's residential-grade IPs to bypass
 * Amazon's datacenter IP blocks. Free: 100K req/day.
 *
 * Deploy:
 *   1. npm install -g wrangler
 *   2. wrangler login
 *   3. cd worker && wrangler deploy
 *   4. Copy the worker URL (e.g. https://pricescout-proxy.YOUR_SUBDOMAIN.workers.dev)
 *   5. Set PROXY_URL env var in Vercel to that URL
 *
 * Usage:
 *   GET  /proxy?url=https://www.amazon.in/dp/B0XXX&ua=Mozilla/5.0...
 *   POST /proxy  { "url": "...", "ua": "...", "headers": {...} }
 */

export default {
  async fetch(request) {
    // CORS headers for all responses
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/") {
      return new Response("PriceScout proxy is running", {
        headers: { ...corsHeaders, "Content-Type": "text/plain" },
      });
    }

    if (url.pathname !== "/proxy") {
      return new Response("Not found. Use /proxy?url=...", {
        status: 404,
        headers: corsHeaders,
      });
    }

    let targetUrl, userAgent, extraHeaders;

    if (request.method === "POST") {
      try {
        const body = await request.json();
        targetUrl = body.url;
        userAgent = body.ua || "";
        extraHeaders = body.headers || {};
      } catch {
        return new Response("Invalid JSON body", {
          status: 400,
          headers: corsHeaders,
        });
      }
    } else {
      targetUrl = url.searchParams.get("url");
      userAgent = url.searchParams.get("ua") || "";
      extraHeaders = {};
    }

    if (!targetUrl) {
      return new Response("Missing ?url= parameter", {
        status: 400,
        headers: corsHeaders,
      });
    }

    // Validate URL is an allowed domain (security: don't proxy arbitrary URLs)
    const targetHost = new URL(targetUrl).hostname;
    const allowed = [
      "amazon.in",
      "amazon.com",
      "flipkart.com",
      "www.amazon.in",
      "www.amazon.com",
      "www.flipkart.com",
    ];
    if (!allowed.some((h) => targetHost === h || targetHost.endsWith("." + h))) {
      return new Response("Domain not allowed: " + targetHost, {
        status: 403,
        headers: corsHeaders,
      });
    }

    try {
      // Build headers for the proxied request
      const fetchHeaders = {
        "User-Agent":
          userAgent ||
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        Accept:
          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        ...extraHeaders,
      };

      const resp = await fetch(targetUrl, {
        headers: fetchHeaders,
        redirect: "follow",
      });

      const body = await resp.text();

      return new Response(body, {
        status: resp.status,
        headers: {
          ...corsHeaders,
          "Content-Type": resp.headers.get("Content-Type") || "text/html",
        },
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 502,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }
  },
};
