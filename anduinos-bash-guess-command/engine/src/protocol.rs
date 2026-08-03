//! Small dependency-free protocol for the persistent Bash coprocess.
//!
//! Each request and response occupies one line. User-controlled bytes are hex
//! encoded, so commands can never inject protocol fields or terminal control
//! sequences.

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Request {
    Query {
        now_ms: u64,
        line: String,
    },
    Observe {
        exit_code: i32,
        now_ms: u64,
        line: String,
        cwd: String,
    },
    Ping,
    Quit,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Response {
    Suggestion {
        insertion: String,
        confidence_milli: u16,
        source: String,
    },
    None {
        authoritative: bool,
    },
    Ack,
    Pong,
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProtocolError;

pub fn decode_request(line: &str) -> Result<Request, ProtocolError> {
    let fields: Vec<&str> = line.trim_end_matches(['\r', '\n']).split('\t').collect();
    match fields.as_slice() {
        ["Q", now_ms, encoded] => Ok(Request::Query {
            now_ms: now_ms.parse().map_err(|_| ProtocolError)?,
            line: decode_hex(encoded)?,
        }),
        ["O", exit_code, now_ms, encoded, cwd] => Ok(Request::Observe {
            exit_code: exit_code.parse().map_err(|_| ProtocolError)?,
            now_ms: now_ms.parse().map_err(|_| ProtocolError)?,
            line: decode_hex(encoded)?,
            cwd: decode_hex(cwd)?,
        }),
        ["P"] => Ok(Request::Ping),
        ["X"] => Ok(Request::Quit),
        _ => Err(ProtocolError),
    }
}

pub fn encode_request(request: &Request) -> String {
    match request {
        Request::Query { now_ms, line } => format!("Q\t{now_ms}\t{}\n", encode_hex(line)),
        Request::Observe {
            exit_code,
            now_ms,
            line,
            cwd,
        } => format!(
            "O\t{exit_code}\t{now_ms}\t{}\t{}\n",
            encode_hex(line),
            encode_hex(cwd)
        ),
        Request::Ping => "P\n".into(),
        Request::Quit => "X\n".into(),
    }
}

pub fn encode_response(response: &Response) -> String {
    match response {
        Response::Suggestion {
            insertion,
            confidence_milli,
            source,
        } => format!(
            "S\t{}\t{confidence_milli}\t{}\n",
            encode_hex(insertion),
            encode_hex(source)
        ),
        Response::None { authoritative } => {
            format!("N\t{}\n", u8::from(*authoritative))
        }
        Response::Ack => "A\n".into(),
        Response::Pong => "P\n".into(),
        Response::Error => "E\n".into(),
    }
}

pub fn decode_response(line: &str) -> Result<Response, ProtocolError> {
    let fields: Vec<&str> = line.trim_end_matches(['\r', '\n']).split('\t').collect();
    match fields.as_slice() {
        ["S", insertion, confidence, source] => Ok(Response::Suggestion {
            insertion: decode_hex(insertion)?,
            confidence_milli: confidence.parse().map_err(|_| ProtocolError)?,
            source: decode_hex(source)?,
        }),
        ["N", "0"] => Ok(Response::None {
            authoritative: false,
        }),
        ["N", "1"] => Ok(Response::None {
            authoritative: true,
        }),
        ["A"] => Ok(Response::Ack),
        ["P"] => Ok(Response::Pong),
        ["E"] => Ok(Response::Error),
        _ => Err(ProtocolError),
    }
}

fn encode_hex(value: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(value.len() * 2);
    for byte in value.bytes() {
        encoded.push(HEX[(byte >> 4) as usize] as char);
        encoded.push(HEX[(byte & 0x0f) as usize] as char);
    }
    encoded
}

fn decode_hex(value: &str) -> Result<String, ProtocolError> {
    if !value.len().is_multiple_of(2) {
        return Err(ProtocolError);
    }
    let mut decoded = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        decoded.push((nibble(pair[0])? << 4) | nibble(pair[1])?);
    }
    String::from_utf8(decoded).map_err(|_| ProtocolError)
}

fn nibble(byte: u8) -> Result<u8, ProtocolError> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        b'A'..=b'F' => Ok(byte - b'A' + 10),
        _ => Err(ProtocolError),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_round_trip_handles_unicode_and_protocol_characters() {
        let request = Request::Query {
            now_ms: 42,
            line: "docker exec 容器\t'quoted'".into(),
        };
        assert_eq!(decode_request(&encode_request(&request)), Ok(request));
    }

    #[test]
    fn malformed_or_non_utf8_payload_is_rejected() {
        assert!(decode_request("Q\t1\t0").is_err());
        assert!(decode_request("Q\t1\tff").is_err());
        assert!(decode_request("Q\tbogus\t00").is_err());
    }

    #[test]
    fn response_round_trip_never_exposes_raw_control_bytes() {
        let response = Response::Suggestion {
            insertion: "kind_bassi".into(),
            confidence_milli: 930,
            source: "LiveEntity".into(),
        };
        let wire = encode_response(&response);
        assert!(!wire.contains("kind_bassi"));
        assert_eq!(decode_response(&wire), Ok(response));
    }
}
