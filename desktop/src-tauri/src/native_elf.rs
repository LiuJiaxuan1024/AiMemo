#![cfg(target_os = "linux")]

use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::process::Command;
use std::rc::Rc;
use std::thread;
use std::time::Duration;

use gdk::cairo::{Context, RectangleInt, Region};
use gdk::prelude::*;
use gdk_pixbuf::Pixbuf;
use gtk::glib::{self, ControlFlow, Priority};
use gtk::prelude::*;

const ELF_WIDTH: i32 = 260;
const BUBBLE_HEIGHT: i32 = 54;
const CHAT_HEIGHT: i32 = 46;
const WINDOW_PADDING: i32 = 8;
const ALPHA_THRESHOLD: u8 = 12;
const DEV_MEMO_URL: &str = "http://127.0.0.1:5173/app/memo";
const ELF_CHAT_URL: &str = "http://127.0.0.1:8000/api/elf/chat/stream";
const ELF_EVENTS_URL: &str = "http://127.0.0.1:8000/api/elf/events";
const DEFAULT_EXPRESSION: &str = "01_idle_soft.png";

#[derive(Debug)]
enum NativeUiMessage {
    Bubble { text: String, expression: String },
    Expression { expression: String },
}

#[derive(Debug)]
struct ChatBubblePart {
    text: String,
    expression: String,
}

fn main() {
    gtk::init().expect("failed to initialize GTK");
    install_css();

    let pixbuf = load_elf_pixbuf(DEFAULT_EXPRESSION);
    let pixbuf = pixbuf
        .scale_simple(
            ELF_WIDTH,
            pixbuf.height() * ELF_WIDTH / pixbuf.width(),
            gdk_pixbuf::InterpType::Bilinear,
        )
        .expect("failed to scale Memo Elf image");
    let width = pixbuf.width() + WINDOW_PADDING * 2;
    let height = pixbuf.height() + BUBBLE_HEIGHT + CHAT_HEIGHT + WINDOW_PADDING * 2;

    let window = gtk::Window::builder()
        .title("Memo Elf")
        .decorated(false)
        .resizable(false)
        .app_paintable(true)
        .default_width(width)
        .default_height(height)
        .build();
    window.set_keep_above(true);
    window.set_skip_taskbar_hint(true);
    window.set_accept_focus(true);
    window.set_type_hint(gdk::WindowTypeHint::Utility);
    window.set_size_request(width, height);
    window.move_(1160, 520);

    if let Some(screen) = gdk::Screen::default() {
        if let Some(visual) = screen.rgba_visual() {
            window.set_visual(Some(&visual));
        }
    }

    let bubble_visible = Rc::new(Cell::new(true));
    let chat_visible = Rc::new(Cell::new(false));
    let bubble_text = Rc::new(RefCell::new(String::from("我在这里，点气泡和我说话。")));
    let current_pixbuf = Rc::new(RefCell::new(pixbuf.clone()));
    let drawing_area = gtk::DrawingArea::builder()
        .app_paintable(true)
        .width_request(width)
        .height_request(height)
        .build();
    drawing_area.add_events(
        gdk::EventMask::BUTTON_PRESS_MASK
            | gdk::EventMask::BUTTON_RELEASE_MASK
            | gdk::EventMask::POINTER_MOTION_MASK,
    );

    let draw_current_pixbuf = Rc::clone(&current_pixbuf);
    let draw_bubble_visible = Rc::clone(&bubble_visible);
    let draw_bubble_text = Rc::clone(&bubble_text);
    drawing_area.connect_draw(move |_, context| {
        clear_context(context);
        if draw_bubble_visible.get() {
            draw_bubble(context, width, &draw_bubble_text.borrow());
        }
        context.set_source_pixbuf(
            &draw_current_pixbuf.borrow(),
            WINDOW_PADDING as f64,
            (BUBBLE_HEIGHT + WINDOW_PADDING) as f64,
        );
        context.paint().expect("failed to draw Memo Elf image");
        glib::Propagation::Proceed
    });

    let fixed = gtk::Fixed::builder()
        .app_paintable(true)
        .width_request(width)
        .height_request(height)
        .build();
    fixed.put(&drawing_area, 0, 0);

    let chat_entry = gtk::Entry::builder()
        .placeholder_text("想和我说什么？回车发送")
        .width_request(width - WINDOW_PADDING * 2)
        .height_request(34)
        .build();
    chat_entry.style_context().add_class("native-chat-entry");
    chat_entry.set_no_show_all(true);
    fixed.put(&chat_entry, WINDOW_PADDING, height - CHAT_HEIGHT + 5);

    let drag_window = window.clone();
    let press_chat_visible = Rc::clone(&chat_visible);
    let press_entry = chat_entry.clone();
    drawing_area.connect_button_press_event(move |_, event| {
        if event.button() != 1 {
            return glib::Propagation::Proceed;
        }
        if event.event_type() == gdk::EventType::DoubleButtonPress {
            open_aimemo();
            return glib::Propagation::Stop;
        }
        if event.position().1 <= BUBBLE_HEIGHT as f64 {
            let is_visible = !press_chat_visible.get();
            press_chat_visible.set(is_visible);
            if is_visible {
                press_entry.show();
                press_entry.grab_focus();
            } else {
                press_entry.hide();
            }
            return glib::Propagation::Stop;
        }
        let (root_x, root_y) = event.root();
        drag_window.begin_move_drag(event.button() as i32, root_x as i32, root_y as i32, event.time());
        glib::Propagation::Stop
    });

    let click_bubble_visible = Rc::clone(&bubble_visible);
    let click_area = drawing_area.clone();
    drawing_area.connect_button_release_event(move |_, event| {
        if event.button() == 1 && event.position().1 > BUBBLE_HEIGHT as f64 {
            click_bubble_visible.set(!click_bubble_visible.get());
            click_area.queue_draw();
        }
        glib::Propagation::Proceed
    });

    let (sender, receiver) = glib::MainContext::channel::<NativeUiMessage>(Priority::DEFAULT);
    let entry_area = drawing_area.clone();
    let entry_bubble_text = Rc::clone(&bubble_text);
    let entry_bubble_visible = Rc::clone(&bubble_visible);
    let entry_chat_visible = Rc::clone(&chat_visible);
    let entry_pixbuf = Rc::clone(&current_pixbuf);
    let entry_window = window.clone();
    let chat_sender = sender.clone();
    chat_entry.connect_activate(move |entry| {
        let message = entry.text().trim().to_string();
        if message.is_empty() {
            return;
        }
        entry.set_text("");
        entry.hide();
        entry_chat_visible.set(false);
        entry_bubble_visible.set(true);
        *entry_bubble_text.borrow_mut() = String::from("嗯，我听着。");
        set_expression(
            &entry_window,
            &entry_pixbuf,
            &entry_area,
            width,
            height,
            expression_from_emoji("thinking"),
        );
        entry_area.queue_draw();

        let sender = chat_sender.clone();
        thread::spawn(move || {
            let message = match send_elf_chat(&message) {
                Ok(part) => NativeUiMessage::Bubble {
                    text: part.text,
                    expression: part.expression,
                },
                Err(error) => NativeUiMessage::Bubble {
                    text: format!("刚才没连上对话服务：{error}"),
                    expression: expression_from_mood("error").to_string(),
                },
            };
            let _ = sender.send(message);
        });
    });

    let receive_area = drawing_area.clone();
    let receive_bubble_text = Rc::clone(&bubble_text);
    let receive_bubble_visible = Rc::clone(&bubble_visible);
    let receive_pixbuf = Rc::clone(&current_pixbuf);
    let receive_window = window.clone();
    receiver.attach(None, move |message| {
        match message {
            NativeUiMessage::Bubble { text, expression } => {
                *receive_bubble_text.borrow_mut() = text;
                receive_bubble_visible.set(true);
                set_expression(&receive_window, &receive_pixbuf, &receive_area, width, height, &expression);
            }
            NativeUiMessage::Expression { expression } => {
                set_expression(&receive_window, &receive_pixbuf, &receive_area, width, height, &expression);
            }
        }
        receive_area.queue_draw();
        ControlFlow::Continue
    });

    start_event_polling(sender.clone());

    window.add(&fixed);

    let shape_pixbuf = pixbuf.clone();
    window.connect_realize(move |window| {
        apply_window_shape(window, &shape_pixbuf, width, height);
    });

    window.connect_delete_event(|_, _| {
        gtk::main_quit();
        glib::Propagation::Proceed
    });

    window.show_all();
    gtk::main();
}

fn install_css() {
    let provider = gtk::CssProvider::new();
    provider
        .load_from_data(
            b"
            entry.native-chat-entry {
              background: rgba(255, 255, 255, 0.96);
              border: 1px solid rgba(124, 179, 255, 0.86);
              border-radius: 8px;
              color: #0f172a;
              padding: 8px 10px;
            }
            ",
        )
        .expect("failed to install native elf CSS");
    if let Some(screen) = gdk::Screen::default() {
        gtk::StyleContext::add_provider_for_screen(&screen, &provider, gtk::STYLE_PROVIDER_PRIORITY_APPLICATION);
    }
}

fn load_elf_pixbuf(filename: &str) -> Pixbuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let image_path = manifest_dir
        .parent()
        .expect("src-tauri should live under desktop")
        .join("public/elf/memo")
        .join(filename);
    Pixbuf::from_file(&image_path)
        .unwrap_or_else(|error| panic!("failed to load {}: {error}", image_path.display()))
}

fn load_scaled_expression(filename: &str) -> Pixbuf {
    let pixbuf = load_elf_pixbuf(filename);
    pixbuf
        .scale_simple(
            ELF_WIDTH,
            pixbuf.height() * ELF_WIDTH / pixbuf.width(),
            gdk_pixbuf::InterpType::Bilinear,
        )
        .expect("failed to scale Memo Elf image")
}

fn set_expression(
    window: &gtk::Window,
    current_pixbuf: &Rc<RefCell<Pixbuf>>,
    drawing_area: &gtk::DrawingArea,
    width: i32,
    height: i32,
    expression: &str,
) {
    let pixbuf = load_scaled_expression(expression);
    *current_pixbuf.borrow_mut() = pixbuf;
    apply_window_shape(window, &current_pixbuf.borrow(), width, height);
    drawing_area.queue_draw();
}

fn clear_context(context: &Context) {
    context.set_operator(gdk::cairo::Operator::Clear);
    context.paint().expect("failed to clear native elf window");
    context.set_operator(gdk::cairo::Operator::Over);
}

fn draw_bubble(context: &Context, width: i32, text: &str) {
    let bubble_width = 226.0;
    let bubble_height = 36.0;
    let x = ((width as f64 - bubble_width) / 2.0).round();
    let y = 6.0;

    rounded_rect(context, x, y, bubble_width, bubble_height, 7.0);
    context.set_source_rgba(0.93, 0.97, 1.0, 0.96);
    context.fill_preserve().expect("failed to fill native elf bubble");
    context.set_source_rgba(0.49, 0.70, 1.0, 0.88);
    context.set_line_width(1.0);
    context.stroke().expect("failed to stroke native elf bubble");

    context.move_to(x + bubble_width - 54.0, y + bubble_height - 1.0);
    context.line_to(x + bubble_width - 44.0, y + bubble_height + 9.0);
    context.line_to(x + bubble_width - 34.0, y + bubble_height - 1.0);
    context.close_path();
    context.set_source_rgba(0.93, 0.97, 1.0, 0.96);
    context.fill_preserve().expect("failed to fill native elf bubble tail");
    context.set_source_rgba(0.49, 0.70, 1.0, 0.88);
    context.stroke().expect("failed to stroke native elf bubble tail");

    context.select_font_face("Sans", gdk::cairo::FontSlant::Normal, gdk::cairo::FontWeight::Normal);
    context.set_font_size(13.0);
    context.set_source_rgb(0.10, 0.29, 0.66);
    context.move_to(x + 13.0, y + 22.0);
    let _ = context.show_text(&ellipsize(text, 18));
}

fn rounded_rect(context: &Context, x: f64, y: f64, width: f64, height: f64, radius: f64) {
    let degrees = std::f64::consts::PI / 180.0;
    context.new_sub_path();
    context.arc(x + width - radius, y + radius, radius, -90.0 * degrees, 0.0 * degrees);
    context.arc(x + width - radius, y + height - radius, radius, 0.0 * degrees, 90.0 * degrees);
    context.arc(x + radius, y + height - radius, radius, 90.0 * degrees, 180.0 * degrees);
    context.arc(x + radius, y + radius, radius, 180.0 * degrees, 270.0 * degrees);
    context.close_path();
}

fn apply_window_shape(window: &gtk::Window, pixbuf: &Pixbuf, width: i32, _height: i32) {
    let Some(gdk_window) = window.window() else {
        return;
    };

    let region = Region::create_rectangle(&RectangleInt::new(0, 0, width, BUBBLE_HEIGHT + WINDOW_PADDING * 2));
    let _ = region.union_rectangle(&RectangleInt::new(WINDOW_PADDING, _height - CHAT_HEIGHT, width - WINDOW_PADDING * 2, CHAT_HEIGHT));
    union_pixbuf_alpha_runs(&region, pixbuf, WINDOW_PADDING, BUBBLE_HEIGHT + WINDOW_PADDING);
    gdk_window.shape_combine_region(Some(&region), 0, 0);
    gdk_window.input_shape_combine_region(&region, 0, 0);
}

fn union_pixbuf_alpha_runs(region: &Region, pixbuf: &Pixbuf, offset_x: i32, offset_y: i32) {
    let width = pixbuf.width();
    let height = pixbuf.height();
    let channels = pixbuf.n_channels() as usize;
    let rowstride = pixbuf.rowstride() as usize;
    let bytes = pixbuf.read_pixel_bytes();
    let pixels = bytes.as_ref();

    if channels < 4 || !pixbuf.has_alpha() {
        let _ = region.union_rectangle(&RectangleInt::new(offset_x, offset_y, width, height));
        return;
    }

    for y in 0..height {
        let mut run_start: Option<i32> = None;
        for x in 0..width {
            let alpha_index = y as usize * rowstride + x as usize * channels + 3;
            let is_opaque = pixels.get(alpha_index).copied().unwrap_or(0) > ALPHA_THRESHOLD;
            match (run_start, is_opaque) {
                (None, true) => run_start = Some(x),
                (Some(start), false) => {
                    let _ = region.union_rectangle(&RectangleInt::new(offset_x + start, offset_y + y, x - start, 1));
                    run_start = None;
                }
                _ => {}
            }
        }
        if let Some(start) = run_start {
            let _ = region.union_rectangle(&RectangleInt::new(offset_x + start, offset_y + y, width - start, 1));
        }
    }
}

fn open_aimemo() {
    let _ = Command::new("xdg-open")
        .arg(DEV_MEMO_URL)
        .spawn();
}

fn send_elf_chat(message: &str) -> Result<ChatBubblePart, Box<dyn std::error::Error + Send + Sync>> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(90))
        .build()?;
    let response = client
        .post(ELF_CHAT_URL)
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(serde_json::json!({ "message": message }).to_string())
        .send()?;
    if !response.status().is_success() {
        return Err(format!("HTTP {}", response.status()).into());
    }

    let body = response.text()?;
    Ok(parse_elf_sse_answer(&body))
}

fn parse_elf_sse_answer(body: &str) -> ChatBubblePart {
    let mut answer = String::new();
    let mut bubbles: Vec<ChatBubblePart> = Vec::new();
    for block in body.split("\n\n") {
        let mut event_name = "";
        let mut data = "";
        for line in block.lines() {
            if let Some(value) = line.strip_prefix("event:") {
                event_name = value.trim();
            }
            if let Some(value) = line.strip_prefix("data:") {
                data = value.trim();
            }
        }
        if data.is_empty() {
            continue;
        }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(data) else {
            continue;
        };
        match event_name {
            "answer_delta" => {
                if let Some(content) = value.get("content").and_then(|content| content.as_str()) {
                    answer.push_str(content);
                }
            }
            "done" => {
                if let Some(raw_bubbles) = value.get("bubbles").and_then(|bubbles| bubbles.as_array()) {
                    bubbles.extend(raw_bubbles.iter().filter_map(parse_chat_bubble));
                }
            }
            "error" => {
                if let Some(message) = value.get("message").and_then(|message| message.as_str()) {
                    return ChatBubblePart {
                        text: message.to_string(),
                        expression: expression_from_mood("error").to_string(),
                    };
                }
            }
            _ => {}
        }
    }

    if !bubbles.is_empty() {
        let expression = bubbles
            .first()
            .map(|bubble| bubble.expression.clone())
            .unwrap_or_else(|| DEFAULT_EXPRESSION.to_string());
        return ChatBubblePart {
            text: bubbles.into_iter().map(|bubble| bubble.text).collect::<Vec<_>>().join(" "),
            expression,
        };
    }
    if answer.trim().is_empty() {
        ChatBubblePart {
            text: String::from("我刚才有点走神了，再说一次好吗？"),
            expression: expression_from_emoji("confused").to_string(),
        }
    } else {
        ChatBubblePart {
            text: answer.trim().to_string(),
            expression: expression_from_mood("talking").to_string(),
        }
    }
}

fn parse_chat_bubble(value: &serde_json::Value) -> Option<ChatBubblePart> {
    let text = value.get("text")?.as_str()?.trim();
    if text.is_empty() {
        return None;
    }
    let emoji = value
        .get("emoji")
        .and_then(|emoji| emoji.as_str())
        .unwrap_or("idle_soft");
    Some(ChatBubblePart {
        text: text.to_string(),
        expression: expression_from_emoji(emoji).to_string(),
    })
}

fn start_event_polling(sender: glib::Sender<NativeUiMessage>) {
    thread::spawn(move || {
        let client = match reqwest::blocking::Client::builder()
            .timeout(Duration::from_millis(1200))
            .build()
        {
            Ok(client) => client,
            Err(_) => return,
        };
        let mut last_event_id = 0_i64;
        loop {
            if let Ok(response) = client
                .get(format!("{ELF_EVENTS_URL}?after_id={last_event_id}&limit=20"))
                .send()
            {
                if let Ok(body) = response.text() {
                    let Ok(payload) = serde_json::from_str::<serde_json::Value>(&body) else {
                        thread::sleep(Duration::from_secs(1));
                        continue;
                    };
                    if let Some(events) = payload.get("events").and_then(|events| events.as_array()) {
                        for event in events {
                            if let Some(id) = event.get("id").and_then(|id| id.as_i64()) {
                                last_event_id = last_event_id.max(id);
                            }
                            let mood = event.get("mood").and_then(|mood| mood.as_str()).unwrap_or("idle");
                            let expression = expression_from_mood(mood).to_string();
                            if let Some(message) = event.get("message").and_then(|message| message.as_str()) {
                                if !message.trim().is_empty() {
                                    let _ = sender.send(NativeUiMessage::Bubble {
                                        text: message.trim().to_string(),
                                        expression,
                                    });
                                    continue;
                                }
                            }
                            let _ = sender.send(NativeUiMessage::Expression { expression });
                        }
                    }
                }
            }
            thread::sleep(Duration::from_secs(1));
        }
    });
}

fn expression_from_mood(mood: &str) -> &'static str {
    match mood {
        "thinking" => "02_thinking.png",
        "working" => "03_working_focus.png",
        "success" => "04_success_smile.png",
        "warning" | "error" => "05_error_worried.png",
        "talking" => "07_curious.png",
        "idle" => DEFAULT_EXPRESSION,
        _ => DEFAULT_EXPRESSION,
    }
}

fn expression_from_emoji(emoji: &str) -> &'static str {
    match emoji {
        "thinking" => "02_thinking.png",
        "working_focus" => "03_working_focus.png",
        "success_smile" => "04_success_smile.png",
        "error_worried" => "05_error_worried.png",
        "sleepy" => "06_sleepy.png",
        "curious" => "07_curious.png",
        "memory_glow" => "08_memory_glow.png",
        "shy_blush" => "09_shy_blush.png",
        "angry_pout" => "10_angry_pout.png",
        "surprised" => "11_surprised.png",
        "sad_teary" => "12_sad_teary.png",
        "wronged_pout" => "13_wronged_pout.png",
        "confused" => "14_confused.png",
        "proud" => "15_proud.png",
        "playful_wink" => "16_playful_wink.png",
        "serious" => "17_serious.png",
        "relaxed" => "18_relaxed.png",
        "encouraging" => "19_encouraging.png",
        "speechless" => "20_speechless.png",
        "soft" | "idle_soft" => DEFAULT_EXPRESSION,
        "happy" => "04_success_smile.png",
        "worried" => "05_error_worried.png",
        "memory" => "08_memory_glow.png",
        _ => DEFAULT_EXPRESSION,
    }
}

fn ellipsize(text: &str, max_chars: usize) -> String {
    let mut chars = text.chars();
    let mut result: String = chars.by_ref().take(max_chars).collect();
    if chars.next().is_some() {
        result.push('…');
    }
    result
}
