//! A stand-in HTTP/1.1 server on loopback: one canned reply per path, and a
//! record of every request it was sent — **headers** included, because where
//! the key goes is a header.
//!
//! Hand-written rather than a mock crate, as the Anthropic and Ollama
//! adapters' stand-ins are: there is no live call in these tests and none is
//! wanted, so what is measured is exactly the bytes the adapter put on the
//! wire and exactly what it made of the bytes that came back.
#![allow(dead_code)]

use serde_json::Value;
use std::collections::{BTreeMap, HashMap};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

/// What the stand-in answers one path with.
#[derive(Debug, Clone)]
pub struct Reply {
    pub status: u16,
    pub body: String,
    /// Extra response headers (`location`, for a redirect).
    pub headers: Vec<(String, String)>,
}

impl Reply {
    pub fn json(status: u16, body: &Value) -> Reply {
        Reply {
            status,
            body: body.to_string(),
            headers: vec![("content-type".into(), "application/json".into())],
        }
    }

    pub fn redirect(status: u16, location: &str) -> Reply {
        Reply {
            status,
            body: String::new(),
            headers: vec![("location".into(), location.into())],
        }
    }
}

/// One request the stand-in was sent.
#[derive(Debug, Clone)]
pub struct Seen {
    pub method: String,
    /// The path, without its query.
    pub path: String,
    pub query: Option<String>,
    pub headers: BTreeMap<String, String>,
    pub body: Value,
}

pub struct Fake {
    pub base_url: String,
    log: Arc<Mutex<Vec<Seen>>>,
    stop: Arc<AtomicBool>,
}

impl Drop for Fake {
    fn drop(&mut self) {
        // The serving thread is blocked in `accept`; one connection wakes it
        // so it can see the flag. Nothing is joined: a failing test must not
        // also hang.
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.base_url.trim_start_matches("http://"));
    }
}

impl Fake {
    /// A stand-in answering each listed path with its reply, and anything
    /// else with a 404.
    pub fn start(routes: &[(&str, Reply)]) -> Fake {
        let routes: HashMap<String, Reply> = routes
            .iter()
            .map(|(p, r)| (p.to_string(), r.clone()))
            .collect();
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind loopback");
        let base_url = format!("http://{}", listener.local_addr().expect("addr"));
        let log: Arc<Mutex<Vec<Seen>>> = Arc::default();
        let stop = Arc::new(AtomicBool::new(false));
        let (l, s) = (log.clone(), stop.clone());
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                if s.load(Ordering::SeqCst) {
                    return;
                }
                if let Ok(stream) = stream {
                    serve(stream, &routes, &l);
                }
            }
        });
        Fake {
            base_url,
            log,
            stop,
        }
    }

    pub fn seen(&self, path: &str) -> Vec<Seen> {
        self.log
            .lock()
            .expect("log")
            .iter()
            .filter(|s| s.path == path)
            .cloned()
            .collect()
    }

    /// Every request, whatever its path.
    pub fn all(&self) -> Vec<Seen> {
        self.log.lock().expect("log").clone()
    }

    /// The one request to `path`, or a failure naming how many there were.
    pub fn only(&self, path: &str) -> Seen {
        let all = self.seen(path);
        assert_eq!(all.len(), 1, "expected one {path}, saw {}", all.len());
        all.into_iter().next().expect("one")
    }
}

fn serve(mut stream: TcpStream, routes: &HashMap<String, Reply>, log: &Arc<Mutex<Vec<Seen>>>) {
    let Some((method, target, headers, body)) = read_request(&stream) else {
        return;
    };
    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p.to_string(), Some(q.to_string())),
        None => (target, None),
    };
    log.lock().expect("log").push(Seen {
        method,
        path: path.clone(),
        query,
        headers,
        body: serde_json::from_str(&body).unwrap_or(Value::Null),
    });
    let reply = routes.get(&path).cloned().unwrap_or_else(|| {
        Reply::json(
            404,
            &serde_json::json!({"error": {"message": "no such route", "type": "not_found"}}),
        )
    });
    let mut head = format!("HTTP/1.1 {} Canned\r\n", reply.status);
    for (name, value) in &reply.headers {
        head.push_str(&format!("{name}: {value}\r\n"));
    }
    head.push_str(&format!(
        "content-length: {}\r\nconnection: close\r\n\r\n",
        reply.body.len()
    ));
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(reply.body.as_bytes());
    let _ = stream.flush();
}

/// The method, the request target, every header lower-cased, and the body.
fn read_request(stream: &TcpStream) -> Option<(String, String, BTreeMap<String, String>, String)> {
    let mut reader = BufReader::new(stream.try_clone().ok()?);
    let mut line = String::new();
    reader.read_line(&mut line).ok()?;
    let mut parts = line.split_whitespace();
    let method = parts.next()?.to_string();
    let target = parts.next()?.to_string();
    let mut headers = BTreeMap::new();
    let mut length = 0usize;
    loop {
        let mut header = String::new();
        if reader.read_line(&mut header).ok()? == 0 {
            break;
        }
        let header = header.trim_end();
        if header.is_empty() {
            break;
        }
        let Some((name, value)) = header.split_once(':') else {
            continue;
        };
        let (name, value) = (name.trim().to_ascii_lowercase(), value.trim().to_string());
        if name == "content-length" {
            length = value.parse().unwrap_or(0);
        }
        headers.insert(name, value);
    }
    let mut body = vec![0u8; length];
    reader.read_exact(&mut body).ok()?;
    Some((
        method,
        target,
        headers,
        String::from_utf8_lossy(&body).into_owned(),
    ))
}
