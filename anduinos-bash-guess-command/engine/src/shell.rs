#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token {
    pub value: String,
    pub start: usize,
    pub end: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParsedLine {
    pub source: String,
    pub tokens: Vec<Token>,
    pub command_start: usize,
    pub trailing_space: bool,
    pub current_prefix: String,
}

impl ParsedLine {
    pub fn command_tokens(&self) -> &[Token] {
        &self.tokens[self.command_start..]
    }

    pub fn command_values(&self) -> Vec<&str> {
        self.command_tokens()
            .iter()
            .map(|token| token.value.as_str())
            .collect()
    }
}

/// Tokenizes an incomplete shell command without evaluation or expansion.
/// Operators terminate the active simple command; quoted partial tokens remain
/// usable. Command substitution is treated as opaque text.
pub fn parse_line(line: &str, cursor: usize) -> Option<ParsedLine> {
    if cursor > line.len() || !line.is_char_boundary(cursor) {
        return None;
    }
    let source = &line[..cursor];
    if source
        .bytes()
        .any(|byte| byte == b'\n' || byte == b'\r' || byte == 0)
    {
        return None;
    }

    let bytes = source.as_bytes();
    let mut tokens = Vec::new();
    let mut value = String::new();
    let mut token_start = None;
    let mut quote = None;
    let mut escaped = false;
    let mut command_start = 0;
    let mut index = 0;

    while index < bytes.len() {
        let byte = bytes[index];
        if !byte.is_ascii() {
            token_start.get_or_insert(index);
            let character = source[index..].chars().next()?;
            value.push(character);
            index += character.len_utf8();
            continue;
        }
        if escaped {
            value.push(byte as char);
            escaped = false;
            index += 1;
            continue;
        }
        if byte == b'\\' && quote != Some(b'\'') {
            token_start.get_or_insert(index);
            escaped = true;
            index += 1;
            continue;
        }
        if let Some(active) = quote {
            if byte == active {
                quote = None;
            } else {
                value.push(byte as char);
            }
            index += 1;
            continue;
        }
        if byte == b'\'' || byte == b'"' {
            token_start.get_or_insert(index);
            quote = Some(byte);
            index += 1;
            continue;
        }
        if byte.is_ascii_whitespace() {
            finish_token(&mut tokens, &mut value, &mut token_start, index);
            index += 1;
            continue;
        }
        if is_operator_start(byte) {
            finish_token(&mut tokens, &mut value, &mut token_start, index);
            index += operator_len(&bytes[index..]);
            command_start = tokens.len();
            continue;
        }
        token_start.get_or_insert(index);
        value.push(byte as char);
        index += 1;
    }
    if escaped {
        value.push('\\');
    }
    finish_token(&mut tokens, &mut value, &mut token_start, source.len());

    let trailing_space = source
        .as_bytes()
        .last()
        .is_some_and(u8::is_ascii_whitespace);
    let current_prefix = if trailing_space || command_start == tokens.len() {
        String::new()
    } else {
        tokens
            .last()
            .map(|token| token.value.clone())
            .unwrap_or_default()
    };
    command_start = unwrap_command_prefix(&tokens, command_start);

    Some(ParsedLine {
        source: source.to_owned(),
        tokens,
        command_start,
        trailing_space,
        current_prefix,
    })
}

fn finish_token(
    tokens: &mut Vec<Token>,
    value: &mut String,
    start: &mut Option<usize>,
    end: usize,
) {
    if let Some(start) = start.take() {
        tokens.push(Token {
            value: std::mem::take(value),
            start,
            end,
        });
    }
}

fn is_operator_start(byte: u8) -> bool {
    matches!(byte, b'|' | b'&' | b';' | b'<' | b'>')
}

fn operator_len(bytes: &[u8]) -> usize {
    if bytes.len() >= 2 && matches!(&bytes[..2], b"||" | b"&&" | b">>" | b"<<" | b"|&") {
        2
    } else {
        1
    }
}

fn unwrap_command_prefix(tokens: &[Token], mut start: usize) -> usize {
    while start < tokens.len() && is_assignment(&tokens[start].value) {
        start += 1;
    }
    if tokens
        .get(start)
        .is_some_and(|token| token.value == "command" || token.value == "time")
    {
        start += 1;
    }
    if tokens.get(start).is_some_and(|token| token.value == "sudo") {
        start += 1;
        while let Some(token) = tokens.get(start) {
            match token.value.as_str() {
                "-E" | "-H" | "-S" | "-n" | "-k" | "--" => start += 1,
                "-u" | "-g" | "-h" | "-p" => start += 2,
                value if value.starts_with("--user=") || value.starts_with("--group=") => {
                    start += 1
                }
                _ => break,
            }
        }
    }
    while start < tokens.len() && is_assignment(&tokens[start].value) {
        start += 1;
    }
    start.min(tokens.len())
}

fn is_assignment(value: &str) -> bool {
    let Some((name, _)) = value.split_once('=') else {
        return false;
    };
    !name.is_empty()
        && name.bytes().enumerate().all(|(index, byte)| {
            byte == b'_' || byte.is_ascii_alphabetic() || (index > 0 && byte.is_ascii_digit())
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unwraps_assignments_and_sudo() {
        let parsed = parse_line("MODE=dev sudo -E docker exec -it ", 33).unwrap();
        assert_eq!(parsed.command_values(), vec!["docker", "exec", "-it"]);
        assert!(parsed.trailing_space);
    }

    #[test]
    fn uses_last_pipeline_command() {
        let line = "docker ps | grep mysql";
        let parsed = parse_line(line, line.len()).unwrap();
        assert_eq!(parsed.command_values(), vec!["grep", "mysql"]);
    }

    #[test]
    fn keeps_incomplete_quoted_token() {
        let line = "git switch 'feature lo";
        let parsed = parse_line(line, line.len()).unwrap();
        assert_eq!(parsed.current_prefix, "feature lo");
    }

    #[test]
    fn preserves_unicode_without_changing_byte_offsets() {
        let line = "docker exec 容器";
        let parsed = parse_line(line, line.len()).unwrap();
        assert_eq!(parsed.current_prefix, "容器");
        assert_eq!(&line[parsed.tokens.last().unwrap().start..], "容器");
    }
}
