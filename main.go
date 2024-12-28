package main

import (
    "database/sql"
    "encoding/json"
    "log"
    "os"
    "path/filepath"
    "time"
    _ "github.com/mattn/go-sqlite3"
    tgbotapi "github.com/go-telegram-bot-api/telegram-bot-api/v5"
)

// 配置结构体
type Config struct {
    AdminID  int64  `json:"admin_id"`
    BotToken string `json:"bot_token"`
}

type Bot struct {
    api    *tgbotapi.BotAPI
    db     *sql.DB
    config *Config
}

// 加载配置
func loadConfig() (*Config, error) {
    // 获取当前执行文件的目录
    dir, err := filepath.Abs(filepath.Dir(os.Args[0]))
    if err != nil {
        return nil, err
    }

    // 读取配置文件
    file, err := os.ReadFile(filepath.Join(dir, "config.json"))
    if err != nil {
        return nil, err
    }

    var config Config
    if err := json.Unmarshal(file, &config); err != nil {
        return nil, err
    }

    return &config, nil
}

func initDB() (*sql.DB, error) {
    db, err := sql.Open("sqlite3", "bot.db")
    if err != nil {
        return nil, err
    }

    // 创建消息映射表
    _, err = db.Exec(`
        CREATE TABLE IF NOT EXISTS message_map (
            message_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    `)
    if err != nil {
        return nil, err
    }

    // 创建用户状态表
    _, err = db.Exec(`
        CREATE TABLE IF NOT EXISTS user_status (
            user_id INTEGER PRIMARY KEY,
            verified BOOLEAN,
            last_verify TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    `)
    if err != nil {
        return nil, err
    }

    return db, nil
}

func NewBot() (*Bot, error) {
    // 加载配置
    config, err := loadConfig()
    if err != nil {
        return nil, err
    }

    api, err := tgbotapi.NewBotAPI(config.BotToken)
    if err != nil {
        return nil, err
    }

    db, err := initDB()
    if err != nil {
        return nil, err
    }

    return &Bot{
        api:    api,
        db:     db,
        config: config,
    }, nil
}

func (b *Bot) addMessageMapping(messageID int64, userID int64) error {
    _, err := b.db.Exec("INSERT INTO message_map (message_id, user_id) VALUES (?, ?)", 
        messageID, userID)
    return err
}

func (b *Bot) getUserByMessageID(messageID int64) (int64, error) {
    var userID int64
    err := b.db.QueryRow("SELECT user_id FROM message_map WHERE message_id = ?", messageID).Scan(&userID)
    if err == sql.ErrNoRows {
        return 0, nil
    }
    return userID, err
}

func (b *Bot) setUserStatus(userID int64, verified bool, lastVerify time.Time) error {
    _, err := b.db.Exec(`
        INSERT OR REPLACE INTO user_status (user_id, verified, last_verify)
        VALUES (?, ?, ?)
    `, userID, verified, lastVerify)
    return err
}

func (b *Bot) getUserStatus(userID int64) (bool, time.Time, error) {
    var verified bool
    var lastVerify time.Time
    err := b.db.QueryRow(`
        SELECT verified, last_verify FROM user_status WHERE user_id = ?
    `, userID).Scan(&verified, &lastVerify)
    if err == sql.ErrNoRows {
        return false, time.Time{}, nil
    }
    return verified, lastVerify, err
}

func (b *Bot) cleanupOldData() error {
    yesterday := time.Now().AddDate(0, 0, -1)
    _, err := b.db.Exec(`
        DELETE FROM user_status 
        WHERE verified = 0 
        AND last_verify < ?
    `, yesterday)
    return err
}

func (b *Bot) handleCallback(callback *tgbotapi.CallbackQuery) error {
    // 立即响应回调
    _, err := b.api.Request(tgbotapi.NewCallback(callback.ID, ""))
    if err != nil {
        return err
    }

    userID := callback.From.ID
    now := time.Now()

    switch callback.Data {
    case "yes":
        err = b.setUserStatus(userID, true, now)
        if err != nil {
            return err
        }

        // 转发原始消息给管理员
        if callback.Message != nil && callback.Message.ReplyToMessage != nil {
            msg := tgbotapi.NewForward(
                b.config.AdminID,
                callback.Message.Chat.ID,
                callback.Message.ReplyToMessage.MessageID,
            )
            sent, err := b.api.Send(msg)
            if err != nil {
                return err
            }

            // 保存消息映射
            err = b.addMessageMapping(int64(sent.MessageID), userID)
            if err != nil {
                return err
            }
        }

        // 删除验证消息
        deleteMsg := tgbotapi.NewDeleteMessage(callback.Message.Chat.ID, callback.Message.MessageID)
        _, err = b.api.Request(deleteMsg)
        if err != nil {
            return err
        }

        // 发送新的确认消息
        msg := tgbotapi.NewMessage(callback.Message.Chat.ID, "验证成功！您的消息已转发给管理员。您现在可以继续发送消息，所有消息都会转发给管理员。")
        _, err = b.api.Send(msg)
        return err

    case "no":
        err = b.setUserStatus(userID, false, now)
        if err != nil {
            return err
        }

        // 删除验证消息
        deleteMsg := tgbotapi.NewDeleteMessage(callback.Message.Chat.ID, callback.Message.MessageID)
        _, err = b.api.Request(deleteMsg)
        if err != nil {
            return err
        }

        // 发送新的拒绝消息
        msg := tgbotapi.NewMessage(callback.Message.Chat.ID, "抱歉，我们目前只接受商务合作相关的咨询。您可以明天再次尝试验证。")
        _, err = b.api.Send(msg)
        return err
    }

    return nil
}

func (b *Bot) handleMessage(message *tgbotapi.Message) error {
    userID := message.From.ID

    // 如果是管理员消息
    if userID == b.config.AdminID {
        if message.ReplyToMessage != nil {
            // 获取原始用户ID
            targetUserID, err := b.getUserByMessageID(int64(message.ReplyToMessage.MessageID))
            if err != nil {
                return err
            }
            if targetUserID != 0 {
                // 转发回给用户
                copy := tgbotapi.NewCopyMessage(targetUserID, message.Chat.ID, message.MessageID)
                _, err = b.api.Send(copy)
                return err
            }
        }
        return nil
    }

    // 检查用户状态
    verified, lastVerify, err := b.getUserStatus(userID)
    if err != nil {
        return err
    }

    // 如果用户已验证
    if verified {
        // 转发消息给管理员
        msg := tgbotapi.NewForward(
            b.config.AdminID,
            message.Chat.ID,
            message.MessageID,
        )
        sent, err := b.api.Send(msg)
        if err != nil {
            return err
        }

        // 保存消息映射
        return b.addMessageMapping(int64(sent.MessageID), userID)
    }

    // 检查是否在24小时内验证过
    if !lastVerify.IsZero() && time.Since(lastVerify) < 24*time.Hour {
        text := "抱歉，您今天已被拒绝，请明天再试。"
        if verified {
            text = "您已通过验证，可以继续发送消息。"
        }
        msg := tgbotapi.NewMessage(message.Chat.ID, text)
        _, err = b.api.Send(msg)
        return err
    }

    // 发送验证请求
    keyboard := tgbotapi.NewInlineKeyboardMarkup(
        tgbotapi.NewInlineKeyboardRow(
            tgbotapi.NewInlineKeyboardButtonData("是", "yes"),
            tgbotapi.NewInlineKeyboardButtonData("否", "no"),
        ),
    )

    msg := tgbotapi.NewMessage(message.Chat.ID, "您是否要询商务合作？")
    msg.ReplyMarkup = keyboard
    msg.ReplyToMessageID = message.MessageID
    _, err = b.api.Send(msg)
    return err
}

func main() {
    bot, err := NewBot()
    if err != nil {
        log.Fatal(err)
    }
    defer bot.db.Close()

    log.Printf("机器人已启动...")

    // 设置更新配置
    updateConfig := tgbotapi.NewUpdate(0)
    updateConfig.Timeout = 60

    // 获取更新通道
    updates := bot.api.GetUpdatesChan(updateConfig)

    // 启动定期清理任务
    go func() {
        ticker := time.NewTicker(6 * time.Hour)
        defer ticker.Stop()

        for range ticker.C {
            if err := bot.cleanupOldData(); err != nil {
                log.Printf("清理数据失败: %v", err)
            } else {
                log.Printf("执行定期清理任务")
            }
        }
    }()

    // 处理更新
    for update := range updates {
        if update.CallbackQuery != nil {
            if err := bot.handleCallback(update.CallbackQuery); err != nil {
                log.Printf("处理回调失败: %v", err)
            }
        } else if update.Message != nil {
            if err := bot.handleMessage(update.Message); err != nil {
                log.Printf("处理消息失败: %v", err)
            }
        }
    }
} 