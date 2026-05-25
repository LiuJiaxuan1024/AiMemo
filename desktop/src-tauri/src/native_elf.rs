#![cfg(target_os = "linux")]

use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::process::Command;
use std::rc::Rc;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use gdk::cairo::{Context, RectangleInt, Region};
use gdk::prelude::*;
use gdk_pixbuf::Pixbuf;
use gtk::glib::{self, ControlFlow, Priority};
use gtk::prelude::*;

const ELF_WIDTH: i32 = 260;
const BUBBLE_HEIGHT: i32 = 124;
const CHAT_HEIGHT: i32 = 46;
const MENU_WIDTH: i32 = 164;
const MENU_HEIGHT: i32 = 78;
const CHOICE_WIDTH: i32 = 420;
const CHOICE_HEIGHT: i32 = 380;
const CHOICE_GAP: i32 = 10;
const WINDOW_PADDING: i32 = 8;
const ALPHA_THRESHOLD: u8 = 12;
const WORKSHOP_URL: &str = "http://127.0.0.1:8000/app/workshop/jobs";
const ELF_CHAT_URL: &str = "http://127.0.0.1:8000/api/elf/chat/stream";
const ELF_CHAT_RESUME_URL_PREFIX: &str = "http://127.0.0.1:8000/api/elf/chat/turns";
const ELF_EVENTS_URL: &str = "http://127.0.0.1:8000/api/elf/events";
const DEFAULT_EXPRESSION: &str = "01_idle_soft.png";

#[derive(Debug)]
enum NativeUiMessage {
    Bubble { text: String, expression: String },
    EventBubble { text: String, expression: String },
    Expression { expression: String },
    EventExpression { expression: String },
    HideBubble,
    ChatFinished,
    ChoiceSubmitted,
    Choice {
        request: UserInputRequest,
        responder: mpsc::Sender<UserInputAnswer>,
    },
}

#[derive(Debug)]
struct ChatBubblePart {
    text: String,
    expression: String,
}

#[derive(Debug, Clone)]
struct UserInputOption {
    id: String,
    label: String,
    value: String,
    description: String,
    recommended: bool,
}

#[derive(Debug, Clone)]
struct UserInputRequest {
    request_id: String,
    question: String,
    selection_mode: String,
    options: Vec<UserInputOption>,
    allow_other: bool,
    other_label: String,
    other_placeholder: String,
}

#[derive(Debug, Clone)]
struct UserInputAnswer {
    request_id: String,
    selected_option_id: String,
    selected_option_ids: Vec<String>,
    answer: String,
    other_text: String,
}

#[derive(Debug)]
struct ElfInterrupt {
    turn_id: i64,
    request: UserInputRequest,
}

enum ParsedSseOutcome {
    Done(Vec<ChatBubblePart>),
    Interrupted(ElfInterrupt),
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
    let width = (pixbuf.width() + WINDOW_PADDING * 2).max(CHOICE_WIDTH + WINDOW_PADDING * 2);
    let height = pixbuf.height() + BUBBLE_HEIGHT + CHOICE_HEIGHT + CHOICE_GAP + WINDOW_PADDING * 2;
    let sprite_x = (width - pixbuf.width()) / 2;
    let interaction_x = (width - CHOICE_WIDTH) / 2;

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
    let menu_visible = Rc::new(Cell::new(false));
    let chat_busy = Rc::new(Cell::new(false));
    let bubble_text = Rc::new(RefCell::new(String::from("我在这里，点我打开菜单。")));
    let current_pixbuf = Rc::new(RefCell::new(pixbuf.clone()));
    let drag_origin = Rc::new(RefCell::new(None::<(f64, f64)>));
    let drag_started = Rc::new(Cell::new(false));
    let drawing_area = gtk::DrawingArea::builder()
        .app_paintable(true)
        .width_request(width)
        .height_request(height)
        .build();

    let elf_event_box = gtk::EventBox::builder()
        .app_paintable(true)
        .width_request(pixbuf.width())
        .height_request(pixbuf.height())
        .build();
    elf_event_box.set_visible_window(false);
    elf_event_box.add_events(
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
            sprite_x as f64,
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
    fixed.put(&elf_event_box, sprite_x, BUBBLE_HEIGHT + WINDOW_PADDING);

    let chat_entry = gtk::Entry::builder()
        .placeholder_text("想和我说什么？回车发送")
        .width_request(CHOICE_WIDTH)
        .height_request(34)
        .build();
    chat_entry.style_context().add_class("native-chat-entry");
    chat_entry.set_no_show_all(true);
    fixed.put(
        &chat_entry,
        interaction_x,
        BUBBLE_HEIGHT + WINDOW_PADDING + pixbuf.height() + 18,
    );

    let action_menu = gtk::Box::new(gtk::Orientation::Vertical, 6);
    action_menu.style_context().add_class("native-action-menu");
    action_menu.set_size_request(MENU_WIDTH, MENU_HEIGHT);
    action_menu.set_no_show_all(true);
    let chat_button = gtk::Button::with_label("和我聊聊");
    chat_button.style_context().add_class("native-menu-button");
    chat_button.set_size_request(MENU_WIDTH - 16, 28);
    let open_button = gtk::Button::with_label("打开工坊");
    open_button.style_context().add_class("native-menu-button");
    open_button.set_size_request(MENU_WIDTH - 16, 28);
    action_menu.pack_start(&chat_button, false, false, 0);
    action_menu.pack_start(&open_button, false, false, 0);
    fixed.put(&action_menu, sprite_x + pixbuf.width() - MENU_WIDTH, BUBBLE_HEIGHT + WINDOW_PADDING);

    let choice_panel = gtk::Box::new(gtk::Orientation::Vertical, 8);
    choice_panel.style_context().add_class("native-choice-panel");
    choice_panel.set_size_request(CHOICE_WIDTH, CHOICE_HEIGHT);
    choice_panel.set_no_show_all(true);
    fixed.put(
        &choice_panel,
        interaction_x,
        BUBBLE_HEIGHT + WINDOW_PADDING + pixbuf.height() + CHOICE_GAP,
    );

    let drag_window = window.clone();
    let press_drag_origin = Rc::clone(&drag_origin);
    let press_drag_started = Rc::clone(&drag_started);
    elf_event_box.connect_button_press_event(move |_, event| {
        eprintln!(
            "[memo-elf-native] button-press button={} type={:?} pos={:?}",
            event.button(),
            event.event_type(),
            event.position()
        );
        if event.button() != 1 {
            return glib::Propagation::Proceed;
        }
        if event.event_type() == gdk::EventType::DoubleButtonPress {
            // Linux 原生桌宠使用单击菜单作为主交互，双击不再执行打开页面，避免误触。
            return glib::Propagation::Stop;
        }
        *press_drag_origin.borrow_mut() = Some(event.position());
        press_drag_started.set(false);
        glib::Propagation::Stop
    });

    let motion_drag_origin = Rc::clone(&drag_origin);
    let motion_drag_started = Rc::clone(&drag_started);
    let motion_menu_visible = Rc::clone(&menu_visible);
    let motion_menu = action_menu.clone();
    elf_event_box.connect_motion_notify_event(move |_, event| {
        let Some((start_x, start_y)) = *motion_drag_origin.borrow() else {
            return glib::Propagation::Proceed;
        };
        if motion_drag_started.get() {
            return glib::Propagation::Stop;
        }
        let (current_x, current_y) = event.position();
        eprintln!(
            "[memo-elf-native] motion pos=({current_x:.1},{current_y:.1}) delta=({:.1},{:.1})",
            current_x - start_x,
            current_y - start_y
        );
        if (current_x - start_x).abs() <= 5.0 && (current_y - start_y).abs() <= 5.0 {
            return glib::Propagation::Stop;
        }
        let (root_x, root_y) = event.root();
        motion_drag_started.set(true);
        motion_menu.hide();
        motion_menu_visible.set(false);
        eprintln!("[memo-elf-native] drag-start root=({root_x:.1},{root_y:.1})");
        drag_window.begin_move_drag(1, root_x as i32, root_y as i32, event.time());
        glib::Propagation::Stop
    });

    let release_drag_origin = Rc::clone(&drag_origin);
    let release_drag_started = Rc::clone(&drag_started);
    let release_menu_visible = Rc::clone(&menu_visible);
    let release_menu = action_menu.clone();
    let release_chat_busy = Rc::clone(&chat_busy);
    elf_event_box.connect_button_release_event(move |_, event| {
        eprintln!(
            "[memo-elf-native] button-release button={} origin={} dragged={} pos={:?}",
            event.button(),
            release_drag_origin.borrow().is_some(),
            release_drag_started.get(),
            event.position()
        );
        if event.button() == 1
            && release_drag_origin.borrow().is_some()
            && !release_drag_started.get()
            && !release_chat_busy.get()
        {
            toggle_action_menu(&release_menu, &release_menu_visible);
        }
        *release_drag_origin.borrow_mut() = None;
        release_drag_started.set(false);
        glib::Propagation::Stop
    });

    let chat_button_entry = chat_entry.clone();
    let chat_button_menu = action_menu.clone();
    let chat_button_chat_visible = Rc::clone(&chat_visible);
    let chat_button_menu_visible = Rc::clone(&menu_visible);
    let chat_button_busy = Rc::clone(&chat_busy);
    chat_button.connect_clicked(move |_| {
        if chat_button_busy.get() {
            return;
        }
        chat_button_menu.hide();
        chat_button_menu_visible.set(false);
        chat_button_chat_visible.set(true);
        chat_button_entry.show();
        chat_button_entry.grab_focus();
    });

    let open_button_menu = action_menu.clone();
    let open_button_menu_visible = Rc::clone(&menu_visible);
    open_button.connect_clicked(move |_| {
        open_button_menu.hide();
        open_button_menu_visible.set(false);
        open_workshop();
    });

    let (sender, receiver) = glib::MainContext::channel::<NativeUiMessage>(Priority::DEFAULT);
    let entry_area = drawing_area.clone();
    let entry_bubble_text = Rc::clone(&bubble_text);
    let entry_bubble_visible = Rc::clone(&bubble_visible);
    let entry_chat_visible = Rc::clone(&chat_visible);
    let entry_menu_visible = Rc::clone(&menu_visible);
    let entry_menu = action_menu.clone();
    let entry_choice_panel = choice_panel.clone();
    let entry_chat_busy = Rc::clone(&chat_busy);
    let entry_pixbuf = Rc::clone(&current_pixbuf);
    let entry_window = window.clone();
    let chat_sender = sender.clone();
    chat_entry.connect_activate(move |entry| {
        let message = entry.text().trim().to_string();
        if message.is_empty() || entry_chat_busy.get() {
            return;
        }
        entry_chat_busy.set(true);
        entry.set_sensitive(false);
        entry.set_text("");
        entry.hide();
        entry_menu.hide();
        entry_choice_panel.hide();
        entry_menu_visible.set(false);
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
            match send_elf_chat(&message, &sender) {
                Ok(parts) => {
                    // Linux 原生桌宠没有 Web 侧的 DOM/CSS 动画，这里在后台线程按气泡顺序投递 UI 消息，
                    // 让 GTK 主线程逐段刷新气泡，避免多个 bubbles 被合并后只显示第一小段。
                    for part in parts {
                        let ttl_ms = bubble_duration_ms(&part.text);
                        let _ = sender.send(NativeUiMessage::Bubble {
                            text: part.text,
                            expression: part.expression,
                        });
                        thread::sleep(Duration::from_millis(ttl_ms));
                    }
                    let _ = sender.send(NativeUiMessage::Expression {
                        expression: DEFAULT_EXPRESSION.to_string(),
                    });
                    let _ = sender.send(NativeUiMessage::HideBubble);
                    let _ = sender.send(NativeUiMessage::ChatFinished);
                }
                Err(error) => {
                    let _ = sender.send(NativeUiMessage::Bubble {
                        text: format!("刚才没连上对话服务：{error}"),
                        expression: expression_from_mood("error").to_string(),
                    });
                    let _ = sender.send(NativeUiMessage::ChatFinished);
                }
            }
        });
    });

    let receive_area = drawing_area.clone();
    let receive_bubble_text = Rc::clone(&bubble_text);
    let receive_bubble_visible = Rc::clone(&bubble_visible);
    let receive_pixbuf = Rc::clone(&current_pixbuf);
    let receive_window = window.clone();
    let receive_menu = action_menu.clone();
    let receive_menu_visible = Rc::clone(&menu_visible);
    let receive_chat_entry = chat_entry.clone();
    let receive_chat_visible = Rc::clone(&chat_visible);
    let receive_chat_busy = Rc::clone(&chat_busy);
    let receive_choice_panel = choice_panel.clone();
    receiver.attach(None, move |message| {
        match message {
            NativeUiMessage::Bubble { text, expression } => {
                *receive_bubble_text.borrow_mut() = text;
                receive_bubble_visible.set(true);
                set_expression(
                    &receive_window,
                    &receive_pixbuf,
                    &receive_area,
                    width,
                    height,
                    &expression,
                );
            }
            NativeUiMessage::EventBubble { text, expression } => {
                if receive_chat_busy.get() {
                    return ControlFlow::Continue;
                }
                *receive_bubble_text.borrow_mut() = text;
                receive_bubble_visible.set(true);
                set_expression(
                    &receive_window,
                    &receive_pixbuf,
                    &receive_area,
                    width,
                    height,
                    &expression,
                );
            }
            NativeUiMessage::Expression { expression } => {
                set_expression(
                    &receive_window,
                    &receive_pixbuf,
                    &receive_area,
                    width,
                    height,
                    &expression,
                );
            }
            NativeUiMessage::EventExpression { expression } => {
                if receive_chat_busy.get() {
                    return ControlFlow::Continue;
                }
                set_expression(
                    &receive_window,
                    &receive_pixbuf,
                    &receive_area,
                    width,
                    height,
                    &expression,
                );
            }
            NativeUiMessage::HideBubble => {
                receive_bubble_visible.set(false);
                set_expression(
                    &receive_window,
                    &receive_pixbuf,
                    &receive_area,
                    width,
                    height,
                    DEFAULT_EXPRESSION,
                );
            }
            NativeUiMessage::ChatFinished => {
                receive_chat_busy.set(false);
                receive_chat_entry.set_sensitive(true);
            }
            NativeUiMessage::ChoiceSubmitted => {
                receive_choice_panel.hide();
                clear_box(&receive_choice_panel);
                receive_bubble_visible.set(true);
                *receive_bubble_text.borrow_mut() = String::from("收到，我继续处理。");
                set_expression(
                    &receive_window,
                    &receive_pixbuf,
                    &receive_area,
                    width,
                    height,
                    expression_from_emoji("working_focus"),
                );
            }
            NativeUiMessage::Choice { request, responder } => {
                let question = request.question.clone();
                receive_menu.hide();
                receive_menu_visible.set(false);
                receive_chat_entry.hide();
                receive_chat_entry.set_sensitive(false);
                receive_chat_visible.set(false);
                receive_bubble_visible.set(true);
                *receive_bubble_text.borrow_mut() = question;
                set_expression(
                    &receive_window,
                    &receive_pixbuf,
                    &receive_area,
                    width,
                    height,
                    expression_from_emoji("curious"),
                );
                show_native_choice_panel(&receive_choice_panel, request, responder);
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
              border: 1px solid rgba(148, 163, 184, 0.86);
              border-radius: 8px;
              color: #0f172a;
              padding: 8px 10px;
            }

            box.native-action-menu {
              background: rgba(255, 255, 255, 0.98);
              border: 1px solid rgba(186, 203, 226, 0.94);
              border-radius: 8px;
              padding: 8px;
            }

            button.native-menu-button {
              min-height: 26px;
              background: transparent;
              border: 1px solid transparent;
              border-radius: 7px;
              color: #1f2a44;
              font-weight: 600;
              padding: 5px 10px;
            }

            button.native-menu-button:hover {
              background: #eff6ff;
              border-color: #bfdbfe;
              color: #1849a9;
            }

            box.native-choice-panel {
              background: linear-gradient(180deg, rgba(20, 28, 47, 0.96), rgba(15, 23, 42, 0.98));
              border: 1px solid rgba(96, 165, 250, 0.68);
              border-radius: 8px;
              padding: 12px;
            }

            label.native-choice-title {
              color: #eff6ff;
              font-size: 13px;
              font-weight: 700;
            }

            label.native-choice-hint,
            label.native-choice-description {
              color: #94a3b8;
              font-size: 11px;
            }

            checkbutton.native-choice-option {
              background: rgba(30, 41, 59, 0.9);
              border: 1px solid rgba(148, 163, 184, 0.28);
              border-radius: 8px;
              color: #e2e8f0;
              padding: 9px 10px;
            }

            checkbutton.native-choice-option:hover,
            checkbutton.native-choice-option:checked {
              background: rgba(37, 99, 235, 0.28);
              border-color: rgba(96, 165, 250, 0.72);
            }

            entry.native-choice-other {
              background: rgba(15, 23, 42, 0.94);
              border: 1px solid rgba(148, 163, 184, 0.36);
              border-radius: 7px;
              color: #f8fafc;
              padding: 7px 8px;
            }

            button.native-choice-submit {
              min-height: 28px;
              background: #60a5fa;
              border: 1px solid #3b82f6;
              border-radius: 7px;
              color: #0f172a;
              font-weight: 600;
              padding: 5px 12px;
            }
            ",
        )
        .expect("failed to install native elf CSS");
    if let Some(screen) = gdk::Screen::default() {
        gtk::StyleContext::add_provider_for_screen(
            &screen,
            &provider,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    }
}

fn toggle_action_menu(menu: &gtk::Box, is_visible: &Rc<Cell<bool>>) {
    let next_visible = !is_visible.get();
    is_visible.set(next_visible);
    eprintln!("[memo-elf-native] action-menu visible={next_visible}");
    if next_visible {
        // GTK 原生菜单是 Linux 桌宠路径的轻量替代：避免再把“点击气泡”直接绑定到聊天输入，
        // 也方便后续继续增加工坊、设置、退出等动作入口。
        menu.show();
        for child in menu.children() {
            child.show();
        }
        eprintln!(
            "[memo-elf-native] action-menu allocated={}x{} mapped={} visible={}",
            menu.allocation().width(),
            menu.allocation().height(),
            menu.is_mapped(),
            menu.is_visible()
        );
        menu.queue_draw();
    } else {
        menu.hide();
    }
}

fn show_native_choice_panel(
    panel: &gtk::Box,
    request: UserInputRequest,
    responder: mpsc::Sender<UserInputAnswer>,
) {
    clear_box(panel);
    let mode = if request.selection_mode == "multiple" {
        "multiple"
    } else {
        "single"
    };

    let selected_ids = Rc::new(RefCell::new(Vec::<String>::new()));
    if let Some(first_option) = request.options.first() {
        selected_ids.borrow_mut().push(first_option.id.clone());
    }
    let buttons = Rc::new(RefCell::new(Vec::<gtk::CheckButton>::new()));

    for option in &request.options {
        let option_title = if option.recommended {
            format!("{}（推荐）", option.label)
        } else {
            option.label.clone()
        };
        let raw_label_text = if option.description.trim().is_empty() {
            option_title
        } else {
            format!("{}\n{}", option_title, option.description)
        };
        let label_text = compact_choice_label(&raw_label_text);
        let button = gtk::CheckButton::with_label(&label_text);
        button.style_context().add_class("native-choice-option");
        button.set_hexpand(true);
        button.set_halign(gtk::Align::Fill);
        button.set_tooltip_text(Some(raw_label_text.as_str()));
        button.set_active(selected_ids.borrow().contains(&option.id));
        let option_id = option.id.clone();
        let mode_for_toggle = mode.to_string();
        let selected_for_toggle = Rc::clone(&selected_ids);
        let buttons_for_toggle = Rc::clone(&buttons);
        button.connect_toggled(move |active_button| {
            sync_native_choice_selection(
                active_button,
                &option_id,
                &mode_for_toggle,
                &selected_for_toggle,
                &buttons_for_toggle,
            );
        });
        buttons.borrow_mut().push(button.clone());
        panel.pack_start(&button, false, false, 0);
    }

    let footer = gtk::Box::new(gtk::Orientation::Vertical, 6);
    footer.style_context().add_class("native-choice-footer");
    let footer_sep = gtk::Separator::new(gtk::Orientation::Horizontal);
    footer.pack_start(&footer_sep, false, false, 0);

    let other_entry = gtk::Entry::builder()
        .placeholder_text(&request.other_placeholder)
        .width_request(CHOICE_WIDTH - WINDOW_PADDING * 4)
        .build();
    other_entry.style_context().add_class("native-choice-other");
    if request.allow_other {
        let other_button = gtk::CheckButton::with_label(&request.other_label);
        other_button
            .style_context()
            .add_class("native-choice-option");
        let option_id = String::from("other");
        let mode_for_toggle = mode.to_string();
        let selected_for_toggle = Rc::clone(&selected_ids);
        let buttons_for_toggle = Rc::clone(&buttons);
        other_button.connect_toggled(move |active_button| {
            sync_native_choice_selection(
                active_button,
                &option_id,
                &mode_for_toggle,
                &selected_for_toggle,
                &buttons_for_toggle,
            );
        });
        let other_button_for_focus = other_button.clone();
        other_entry.connect_focus_in_event(move |_, _| {
            if !other_button_for_focus.is_active() {
                other_button_for_focus.set_active(true);
            }
            glib::Propagation::Proceed
        });
        buttons.borrow_mut().push(other_button.clone());
        footer.pack_start(&other_button, false, false, 0);
        footer.pack_start(&other_entry, false, false, 0);
    }

    let submit_row = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    let submit = gtk::Button::with_label("Submit");
    submit.style_context().add_class("native-choice-submit");
    submit_row.pack_end(&submit, false, false, 0);
    footer.pack_start(&submit_row, false, false, 0);
    panel.pack_start(&footer, false, false, 0);

    let panel_for_submit = panel.clone();
    let selected_for_submit = Rc::clone(&selected_ids);
    let request_for_submit = request.clone();
    let other_entry_for_submit = other_entry.clone();
    submit.connect_clicked(move |_| {
        let mut ids = selected_for_submit.borrow().clone();
        let other_text = other_entry_for_submit.text().trim().to_string();
        if !other_text.is_empty() && !ids.iter().any(|id| id == "other") {
            ids.push(String::from("other"));
        }
        if ids.is_empty() {
            if let Some(first_option) = request_for_submit.options.first() {
                ids.push(first_option.id.clone());
            }
        }
        let mut answer_parts = request_for_submit
            .options
            .iter()
            .filter(|option| ids.iter().any(|id| id == &option.id))
            .map(|option| {
                if option.value.trim().is_empty() {
                    option.label.clone()
                } else {
                    option.value.clone()
                }
            })
            .filter(|value| !value.trim().is_empty())
            .collect::<Vec<_>>();
        if !other_text.is_empty() {
            answer_parts.push(other_text.clone());
        }
        let answer = if answer_parts.is_empty() {
            String::from("继续")
        } else {
            answer_parts.join("\n")
        };
        let selected_option_id = ids.first().cloned().unwrap_or_else(|| String::from("other"));
        let _ = responder.send(UserInputAnswer {
            request_id: request_for_submit.request_id.clone(),
            selected_option_id,
            selected_option_ids: ids,
            answer,
            other_text,
        });
        panel_for_submit.hide();
        clear_box(&panel_for_submit);
    });

    panel.show();
    for child in panel.children() {
        child.show_all();
    }
}

fn sync_native_choice_selection(
    active_button: &gtk::CheckButton,
    option_id: &str,
    mode: &str,
    selected_ids: &Rc<RefCell<Vec<String>>>,
    buttons: &Rc<RefCell<Vec<gtk::CheckButton>>>,
) {
    if active_button.is_active() {
        if mode == "single" {
            selected_ids.borrow_mut().clear();
            selected_ids.borrow_mut().push(option_id.to_string());
            for peer in buttons.borrow().iter() {
                if !peer.eq(active_button) && peer.is_active() {
                    peer.set_active(false);
                }
            }
        } else if !selected_ids.borrow().iter().any(|id| id == option_id) {
            selected_ids.borrow_mut().push(option_id.to_string());
        }
        return;
    }

    selected_ids.borrow_mut().retain(|id| id != option_id);
}

fn compact_choice_label(text: &str) -> String {
    const MAX_LINE_CHARS: usize = 42;
    text.lines()
        .map(|line| compact_line(line.trim(), MAX_LINE_CHARS))
        .collect::<Vec<_>>()
        .join("\n")
}

fn compact_line(text: &str, max_chars: usize) -> String {
    let chars = text.chars().collect::<Vec<_>>();
    if chars.len() <= max_chars {
        return text.to_string();
    }
    let keep_head = max_chars.saturating_sub(3) * 2 / 3;
    let keep_tail = max_chars.saturating_sub(3).saturating_sub(keep_head);
    let head = chars.iter().take(keep_head).collect::<String>();
    let tail = chars
        .iter()
        .skip(chars.len().saturating_sub(keep_tail))
        .collect::<String>();
    format!("{head}...{tail}")
}

fn clear_box(container: &gtk::Box) {
    for child in container.children() {
        container.remove(&child);
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
    let lines = wrap_bubble_text(text);
    let longest_line_chars = lines
        .iter()
        .map(|line| line.chars().count())
        .max()
        .unwrap_or(1);
    let bubble_width = ((longest_line_chars as f64 * 13.0) + 32.0).clamp(132.0, 238.0);
    let bubble_height = (lines.len() as f64 * 20.0 + 20.0).clamp(40.0, 108.0);
    let x = ((width as f64 - bubble_width) / 2.0).round();
    let y = 6.0;

    rounded_rect(context, x, y, bubble_width, bubble_height, 7.0);
    context.set_source_rgba(0.93, 0.97, 1.0, 0.96);
    context
        .fill_preserve()
        .expect("failed to fill native elf bubble");
    context.set_source_rgba(0.49, 0.70, 1.0, 0.88);
    context.set_line_width(1.0);
    context
        .stroke()
        .expect("failed to stroke native elf bubble");

    context.move_to(x + bubble_width - 54.0, y + bubble_height - 1.0);
    context.line_to(x + bubble_width - 44.0, y + bubble_height + 9.0);
    context.line_to(x + bubble_width - 34.0, y + bubble_height - 1.0);
    context.close_path();
    context.set_source_rgba(0.93, 0.97, 1.0, 0.96);
    context
        .fill_preserve()
        .expect("failed to fill native elf bubble tail");
    context.set_source_rgba(0.49, 0.70, 1.0, 0.88);
    context
        .stroke()
        .expect("failed to stroke native elf bubble tail");

    context.select_font_face(
        "Sans",
        gdk::cairo::FontSlant::Normal,
        gdk::cairo::FontWeight::Normal,
    );
    context.set_font_size(13.0);
    context.set_source_rgb(0.10, 0.29, 0.66);
    // Cairo 手绘文本不会像浏览器一样自动换行，所以这里按字符宽度做轻量分行。
    // 中文桌宠气泡的目标是“读得完”，不是严格排版，后续可再切到 Pango 做更精确的文本布局。
    for (index, line) in lines.iter().enumerate() {
        context.move_to(x + 14.0, y + 24.0 + index as f64 * 20.0);
        let _ = context.show_text(line);
    }
}

fn rounded_rect(context: &Context, x: f64, y: f64, width: f64, height: f64, radius: f64) {
    let degrees = std::f64::consts::PI / 180.0;
    context.new_sub_path();
    context.arc(
        x + width - radius,
        y + radius,
        radius,
        -90.0 * degrees,
        0.0 * degrees,
    );
    context.arc(
        x + width - radius,
        y + height - radius,
        radius,
        0.0 * degrees,
        90.0 * degrees,
    );
    context.arc(
        x + radius,
        y + height - radius,
        radius,
        90.0 * degrees,
        180.0 * degrees,
    );
    context.arc(
        x + radius,
        y + radius,
        radius,
        180.0 * degrees,
        270.0 * degrees,
    );
    context.close_path();
}

fn apply_window_shape(window: &gtk::Window, pixbuf: &Pixbuf, width: i32, height: i32) {
    let Some(gdk_window) = window.window() else {
        return;
    };

    let region = Region::create_rectangle(&RectangleInt::new(
        0,
        0,
        width,
        BUBBLE_HEIGHT + WINDOW_PADDING * 2,
    ));
    let _ = region.union_rectangle(&RectangleInt::new(
        ((width - pixbuf.width()) / 2) + pixbuf.width() - MENU_WIDTH,
        BUBBLE_HEIGHT + WINDOW_PADDING,
        MENU_WIDTH,
        MENU_HEIGHT,
    ));
    let _ = region.union_rectangle(&RectangleInt::new(
        (width - CHOICE_WIDTH) / 2,
        height - CHAT_HEIGHT,
        CHOICE_WIDTH,
        CHAT_HEIGHT,
    ));
    let _ = region.union_rectangle(&RectangleInt::new(
        (width - CHOICE_WIDTH) / 2,
        BUBBLE_HEIGHT + WINDOW_PADDING + pixbuf.height() + CHOICE_GAP,
        CHOICE_WIDTH,
        CHOICE_HEIGHT,
    ));
    let _ = region.union_rectangle(&RectangleInt::new(
        (width - pixbuf.width()) / 2,
        BUBBLE_HEIGHT + WINDOW_PADDING,
        pixbuf.width(),
        pixbuf.height(),
    ));
    union_pixbuf_alpha_runs(
        &region,
        pixbuf,
        (width - pixbuf.width()) / 2,
        BUBBLE_HEIGHT + WINDOW_PADDING,
    );
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
                    let _ = region.union_rectangle(&RectangleInt::new(
                        offset_x + start,
                        offset_y + y,
                        x - start,
                        1,
                    ));
                    run_start = None;
                }
                _ => {}
            }
        }
        if let Some(start) = run_start {
            let _ = region.union_rectangle(&RectangleInt::new(
                offset_x + start,
                offset_y + y,
                width - start,
                1,
            ));
        }
    }
}

fn open_workshop() {
    let _ = Command::new("xdg-open").arg(WORKSHOP_URL).spawn();
}

fn send_elf_chat(
    message: &str,
    sender: &glib::Sender<NativeUiMessage>,
) -> Result<Vec<ChatBubblePart>, Box<dyn std::error::Error + Send + Sync>> {
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
    resolve_elf_chat_response(&client, sender, body)
}

fn resolve_elf_chat_response(
    client: &reqwest::blocking::Client,
    sender: &glib::Sender<NativeUiMessage>,
    body: String,
) -> Result<Vec<ChatBubblePart>, Box<dyn std::error::Error + Send + Sync>> {
    match parse_elf_sse_answer(&body)? {
        ParsedSseOutcome::Done(parts) => Ok(parts),
        ParsedSseOutcome::Interrupted(interrupt) => {
            let answer = request_native_user_input(sender, interrupt.request)?;
            let response = client
                .post(format!(
                    "{ELF_CHAT_RESUME_URL_PREFIX}/{}/resume/stream",
                    interrupt.turn_id
                ))
                .header(reqwest::header::CONTENT_TYPE, "application/json")
                .body(user_input_answer_to_json(&answer).to_string())
                .send()?;
            if !response.status().is_success() {
                return Err(format!("resume HTTP {}", response.status()).into());
            }
            resolve_elf_chat_response(client, sender, response.text()?)
        }
    }
}

fn request_native_user_input(
    sender: &glib::Sender<NativeUiMessage>,
    request: UserInputRequest,
) -> Result<UserInputAnswer, Box<dyn std::error::Error + Send + Sync>> {
    let (response_sender, response_receiver) = mpsc::channel();
    sender
        .send(NativeUiMessage::Choice {
            request,
            responder: response_sender,
        })
        .map_err(|_| "failed to show native choice panel")?;
    let answer = response_receiver
        .recv()
        .map_err(|_| "native choice panel was closed before submitting")?;
    let _ = sender.send(NativeUiMessage::ChoiceSubmitted);
    Ok(answer)
}

fn user_input_answer_to_json(answer: &UserInputAnswer) -> serde_json::Value {
    serde_json::json!({
        "request_id": answer.request_id,
        "selected_option_id": answer.selected_option_id,
        "selected_option_ids": answer.selected_option_ids,
        "answer": answer.answer,
        "other_text": answer.other_text,
    })
}

fn parse_elf_sse_answer(
    body: &str,
) -> Result<ParsedSseOutcome, Box<dyn std::error::Error + Send + Sync>> {
    let mut answer = String::new();
    let mut bubbles: Vec<ChatBubblePart> = Vec::new();
    for block in body.split("\n\n") {
        let mut event_name = "";
        let mut data_lines: Vec<&str> = Vec::new();
        for line in block.lines() {
            if let Some(value) = line.strip_prefix("event:") {
                event_name = value.trim();
            }
            if let Some(value) = line.strip_prefix("data:") {
                data_lines.push(value.trim());
            }
        }
        let data = data_lines.join("\n");
        if data.is_empty() {
            continue;
        }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&data) else {
            continue;
        };
        match event_name {
            "answer_delta" => {
                if let Some(content) = value.get("content").and_then(|content| content.as_str()) {
                    answer.push_str(content);
                }
            }
            "done" => {
                if let Some(raw_bubbles) =
                    value.get("bubbles").and_then(|bubbles| bubbles.as_array())
                {
                    bubbles.extend(raw_bubbles.iter().filter_map(parse_chat_bubble));
                }
            }
            "interrupt" => {
                return Ok(ParsedSseOutcome::Interrupted(parse_elf_interrupt(&value)?));
            }
            "error" => {
                if let Some(message) = value.get("message").and_then(|message| message.as_str()) {
                    return Ok(ParsedSseOutcome::Done(vec![ChatBubblePart {
                        text: message.to_string(),
                        expression: expression_from_mood("error").to_string(),
                    }]));
                }
            }
            _ => {}
        }
    }

    if !bubbles.is_empty() {
        return Ok(ParsedSseOutcome::Done(bubbles));
    }
    if answer.trim().is_empty() {
        Ok(ParsedSseOutcome::Done(vec![ChatBubblePart {
            text: String::from("我刚才有点走神了，再说一次好吗？"),
            expression: expression_from_emoji("confused").to_string(),
        }]))
    } else {
        Ok(ParsedSseOutcome::Done(vec![ChatBubblePart {
            text: answer.trim().to_string(),
            expression: expression_from_mood("talking").to_string(),
        }]))
    }
}

fn parse_elf_interrupt(
    value: &serde_json::Value,
) -> Result<ElfInterrupt, Box<dyn std::error::Error + Send + Sync>> {
    let turn_id = value
        .get("turn_id")
        .and_then(|turn_id| {
            turn_id
                .as_i64()
                .or_else(|| turn_id.as_str().and_then(|text| text.parse::<i64>().ok()))
        })
        .unwrap_or(0);
    if turn_id <= 0 {
        return Err("interrupt event did not include a valid turn_id".into());
    }
    Ok(ElfInterrupt {
        turn_id,
        request: normalize_user_input_request(value.get("request")),
    })
}

fn normalize_user_input_request(raw: Option<&serde_json::Value>) -> UserInputRequest {
    let payload = raw.and_then(|value| value.as_object());
    let mut options = payload
        .and_then(|payload| payload.get("options"))
        .and_then(|options| options.as_array())
        .map(|raw_options| {
            raw_options
                .iter()
                .take(6)
                .enumerate()
                .filter_map(|(index, option)| {
                    let item = option.as_object()?;
                    let label = json_string_opt(item.get("label"))
                        .or_else(|| json_string_opt(item.get("value")))
                        .unwrap_or_default();
                    let value = json_string_opt(item.get("value")).unwrap_or_else(|| label.clone());
                    if label.trim().is_empty() && value.trim().is_empty() {
                        return None;
                    }
                    Some(UserInputOption {
                        id: json_string_opt(item.get("id"))
                            .unwrap_or_else(|| format!("option-{}", index + 1)),
                        label: if label.trim().is_empty() {
                            value.clone()
                        } else {
                            label
                        },
                        value,
                        description: json_string_opt(item.get("description")).unwrap_or_default(),
                        recommended: item
                            .get("recommended")
                            .and_then(|recommended| recommended.as_bool())
                            .unwrap_or(index == 0),
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if options.is_empty() {
        options.push(UserInputOption {
            id: String::from("option-1"),
            label: String::from("继续"),
            value: String::from("继续"),
            description: String::from("使用当前上下文继续。"),
            recommended: true,
        });
    }

    let other_option = payload
        .and_then(|payload| payload.get("other_option"))
        .and_then(|value| value.as_object());
    UserInputRequest {
        request_id: payload
            .and_then(|payload| payload.get("request_id"))
            .and_then(json_string_value)
            .unwrap_or_default(),
        question: payload
            .and_then(|payload| payload.get("question"))
            .and_then(json_string_value)
            .unwrap_or_else(|| String::from("请补充一个具体选择。")),
        selection_mode: payload
            .and_then(|payload| payload.get("selection_mode"))
            .and_then(json_string_value)
            .filter(|mode| mode == "multiple")
            .unwrap_or_else(|| String::from("single")),
        options,
        allow_other: payload
            .and_then(|payload| payload.get("allow_other"))
            .and_then(|value| value.as_bool())
            .unwrap_or(true),
        other_label: other_option
            .and_then(|option| option.get("label"))
            .and_then(json_string_value)
            .unwrap_or_else(|| String::from("Other")),
        other_placeholder: other_option
            .and_then(|option| option.get("placeholder"))
            .and_then(json_string_value)
            .unwrap_or_else(|| String::from("请输入其他答案")),
    }
}

fn json_string_opt(value: Option<&serde_json::Value>) -> Option<String> {
    value.and_then(json_string_value)
}

fn json_string_value(value: &serde_json::Value) -> Option<String> {
    match value {
        serde_json::Value::String(text) => Some(text.trim().to_string()),
        serde_json::Value::Number(number) => Some(number.to_string()),
        serde_json::Value::Bool(flag) => Some(flag.to_string()),
        _ => None,
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
                .get(format!(
                    "{ELF_EVENTS_URL}?after_id={last_event_id}&limit=20"
                ))
                .send()
            {
                if let Ok(body) = response.text() {
                    let Ok(payload) = serde_json::from_str::<serde_json::Value>(&body) else {
                        thread::sleep(Duration::from_secs(1));
                        continue;
                    };
                    if let Some(events) = payload.get("events").and_then(|events| events.as_array())
                    {
                        for event in events {
                            if let Some(id) = event.get("id").and_then(|id| id.as_i64()) {
                                last_event_id = last_event_id.max(id);
                            }
                            let mood = event
                                .get("mood")
                                .and_then(|mood| mood.as_str())
                                .unwrap_or("idle");
                            let expression = expression_from_mood(mood).to_string();
                            if let Some(message) =
                                event.get("message").and_then(|message| message.as_str())
                            {
                                if !message.trim().is_empty() {
                                    let _ = sender.send(NativeUiMessage::EventBubble {
                                        text: message.trim().to_string(),
                                        expression,
                                    });
                                    continue;
                                }
                            }
                            let _ = sender.send(NativeUiMessage::EventExpression { expression });
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
        "tsundere_pout" => "21_tsundere_pout.png",
        "smug_grin" => "22_smug_grin.png",
        "chin_thinking" => "23_chin_thinking.png",
        "head_tilt_curious" => "24_head_tilt_curious.png",
        "starry_eyes" => "25_starry_eyes.png",
        "deadpan" => "26_deadpan.png",
        "teasing_smile" => "27_teasing_smile.png",
        "determined" => "28_determined.png",
        "panicked" => "29_panicked.png",
        "comforting_soft" => "30_comforting_soft.png",
        "praying_please" => "31_praying_please.png",
        "tongue_out" => "32_tongue_out.png",
        "mouth_x" => "33_mouth_x.png",
        "dark_aura" => "34_dark_aura.png",
        "sparkle_success" => "35_sparkle_success.png",
        "soft" | "idle_soft" => DEFAULT_EXPRESSION,
        "happy" => "04_success_smile.png",
        "worried" => "05_error_worried.png",
        "memory" => "08_memory_glow.png",
        _ => DEFAULT_EXPRESSION,
    }
}

fn wrap_bubble_text(text: &str) -> Vec<String> {
    let clean_text = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut lines = Vec::new();
    for paragraph in clean_text.split('\n') {
        lines.extend(wrap_bubble_paragraph(paragraph));
    }
    if lines.is_empty() {
        lines.push(String::from("……"));
    }
    if lines.len() > 4 {
        let mut visible = lines.into_iter().take(4).collect::<Vec<_>>();
        if let Some(last) = visible.last_mut() {
            last.push('…');
        }
        visible
    } else {
        lines
    }
}

fn wrap_bubble_paragraph(paragraph: &str) -> Vec<String> {
    const MAX_CHARS: usize = 18;
    const BREAK_PUNCTUATION: [char; 8] = ['。', '！', '？', '；', ';', '!', '?', ','];
    let chars = paragraph.chars().collect::<Vec<_>>();
    if chars.len() <= MAX_CHARS {
        return if paragraph.trim().is_empty() {
            Vec::new()
        } else {
            vec![paragraph.trim().to_string()]
        };
    }

    let mut result = Vec::new();
    let mut start = 0;
    while start < chars.len() {
        let end = (start + MAX_CHARS).min(chars.len());
        if end >= chars.len() {
            result.push(chars[start..end].iter().collect::<String>().trim().to_string());
            break;
        }
        let mut split = None;
        for index in (start..end).rev() {
            if BREAK_PUNCTUATION.contains(&chars[index]) {
                split = Some(index + 1);
                break;
            }
        }
        let split_at = split.unwrap_or(end);
        let line = chars[start..split_at].iter().collect::<String>().trim().to_string();
        if !line.is_empty() {
            result.push(line);
        }
        start = split_at;
    }
    result
}

fn bubble_duration_ms(text: &str) -> u64 {
    let char_count = text.chars().count() as u64;
    (1800 + char_count * 95).clamp(2600, 9000)
}
