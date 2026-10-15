//! The tools this façade exposes, and how each becomes a kernel syscall.
//!
//! Every tool maps to one IPC method — a `harness.*` one, or `driver.act` for
//! the browser — and the façade injects the lease token (`VK_LEASE_TOKEN`)
//! into the call — the model never sees it. The server maps that token to the
//! harness principal at the harness clearance and answers as if the harness
//! itself had made the syscall.

use serde_json::{json, Value};
use vk_ipc::client::Client;

/// The six tools, in the MCP `tools/list` shape. Names are `vk_*`; Claude Code
/// exposes them as `mcp__vk__vk_*`, which `--allowedTools "mcp__vk__*"` admits.
pub fn tool_list() -> Value {
    json!({
        "tools": [
            {
                "name": "vk_read_register",
                "description": "Read the task register the kernel is running: its goal, constraints, plan decisions and evidence, as the harness clearance may see them.",
                "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false }
            },
            {
                "name": "vk_write_decision",
                "description": "Append a decision to the task register.",
                "inputSchema": {
                    "type": "object",
                    "properties": { "text": { "type": "string", "description": "The decision to record." } },
                    "required": ["text"],
                    "additionalProperties": false
                }
            },
            {
                "name": "vk_attach_artefact",
                "description": "Attach a file from the workspace to the register as an artefact of the given kind. Path is relative to the workspace (e.g. OUT/proposal.md).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kind": { "type": "string", "description": "The artefact kind, e.g. \"proposal\"." },
                        "path": { "type": "string", "description": "Workspace-relative path to the file." }
                    },
                    "required": ["kind", "path"],
                    "additionalProperties": false
                }
            },
            {
                "name": "vk_request_approval",
                "description": "Ask a human to approve the task's latest artefact.",
                "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false }
            },
            {
                "name": "vk_log",
                "description": "Record a line in the harness log.",
                "inputSchema": {
                    "type": "object",
                    "properties": { "message": { "type": "string" } },
                    "required": ["message"],
                    "additionalProperties": false
                }
            },
            {
                "name": "vk_driver_act",
                "description": "Act through a driver this task's step declared (a browser the kernel controls). Returns the action's result; evidence is attached to the register by the kernel.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "driver": { "type": "string" },
                        "action": { "type": "string" },
                        "args": { "type": "object" }
                    },
                    "required": ["driver", "action"],
                    "additionalProperties": false
                }
            }
        ]
    })
}

/// Run a tool: map it to its syscall carrying `token` ([`request`]), and
/// render the answer as text. `Err(text)` is a tool error (an unknown tool, or
/// a syscall the kernel refused); the caller turns it into an MCP `isError`
/// result.
pub async fn call(
    client: &Client,
    token: &str,
    name: &str,
    args: &Value,
) -> Result<String, String> {
    let (method, params) = request(token, name, args)?;
    match client.call(method, params, None).await {
        Ok(v) => Ok(render(name, &v)),
        Err(e) => Err(format!("{name} refused by the kernel: {e}")),
    }
}

/// The syscall a tool call becomes: its method, and its params with `token`
/// injected. `vk_driver_act`'s `args` is an empty object when the model gives
/// none.
fn request(token: &str, name: &str, args: &Value) -> Result<(&'static str, Value), String> {
    let str_arg = |key: &str| -> Result<String, String> {
        args.get(key)
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| format!("{name} needs a string `{key}`"))
    };
    Ok(match name {
        "vk_read_register" => ("harness.read_register", json!({ "token": token })),
        "vk_write_decision" => (
            "harness.write_decision",
            json!({ "token": token, "text": str_arg("text")? }),
        ),
        "vk_attach_artefact" => (
            "harness.attach_artefact",
            json!({ "token": token, "kind": str_arg("kind")?, "path": str_arg("path")? }),
        ),
        "vk_request_approval" => ("harness.request_approval", json!({ "token": token })),
        "vk_log" => (
            "harness.log",
            json!({ "token": token, "message": str_arg("message")? }),
        ),
        "vk_driver_act" => (
            "driver.act",
            json!({
                "token": token,
                "driver": str_arg("driver")?,
                "action": str_arg("action")?,
                "args": args.get("args").cloned().unwrap_or(json!({})),
            }),
        ),
        other => return Err(format!("no such tool: {other}")),
    })
}

/// The text a tool's answer reads as for the model.
fn render(name: &str, v: &Value) -> String {
    match name {
        "vk_read_register" => serde_json::to_string_pretty(v).unwrap_or_else(|_| v.to_string()),
        "vk_attach_artefact" => v
            .get("hash")
            .and_then(Value::as_str)
            .map(|h| format!("attached: {h}"))
            .unwrap_or_else(|| "attached".into()),
        "vk_write_decision" => "decision recorded".into(),
        "vk_request_approval" => "approval requested".into(),
        "vk_log" => "logged".into(),
        // The action's result, as the driver gave it; the evidence went to
        // the register, not to the model.
        "vk_driver_act" => {
            serde_json::to_string_pretty(&v["result"]).unwrap_or_else(|_| v["result"].to_string())
        }
        _ => v.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The browser tool: `driver.act` carrying the lease token the model never
    /// sees, the driver and the action by name, and `args` defaulting to an
    /// empty object when the model gives none. The tool is listed with the
    /// schema the brief fixes, and its answer reads as the action's result.
    #[test]
    fn vk_driver_act_maps_to_driver_act_with_the_token_and_default_args() {
        let (method, params) = request(
            "tok-1",
            "vk_driver_act",
            &json!({ "driver": "maskai-1", "action": "look" }),
        )
        .unwrap();
        assert_eq!(method, "driver.act");
        assert_eq!(
            params,
            json!({ "token": "tok-1", "driver": "maskai-1", "action": "look", "args": {} })
        );

        let (_, params) = request(
            "tok-1",
            "vk_driver_act",
            &json!({ "driver": "maskai-1", "action": "send", "args": { "case": "N0-1" } }),
        )
        .unwrap();
        assert_eq!(params["args"], json!({ "case": "N0-1" }));

        let err = request("tok-1", "vk_driver_act", &json!({ "action": "look" })).unwrap_err();
        assert!(err.contains("driver"), "{err}");

        let listed = tool_list();
        let tool = listed["tools"]
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["name"] == "vk_driver_act")
            .expect("vk_driver_act is listed");
        assert_eq!(
            tool["inputSchema"],
            json!({
                "type": "object",
                "properties": {
                    "driver": { "type": "string" },
                    "action": { "type": "string" },
                    "args": { "type": "object" }
                },
                "required": ["driver", "action"],
                "additionalProperties": false
            })
        );
        assert_eq!(
            render(
                "vk_driver_act",
                &json!({ "ok": true, "result": { "text": "hi" } })
            ),
            "{\n  \"text\": \"hi\"\n}"
        );
    }
}
