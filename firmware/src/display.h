#ifndef INKSIGHT_DISPLAY_H
#define INKSIGHT_DISPLAY_H

#include <Arduino.h>

// Look up glyph data for a character (5x7 pixel font)
const uint8_t* getGlyph(char c);

// Draw scaled text into imgBuf at (x, y)
void drawText(const char *msg, int x, int y, int scale);

// Show WiFi setup screen with AP name
void showSetupScreen(const char *apName);

// Show centered error message on screen
void showError(const char *msg);

// Show diagnostic screen with up to 4 lines
void showDiagnostic(const char *line1, const char *line2, const char *line3, const char *line4);

int currentPeriodIndex();

void updateTimeDisplay();

// Smart display: uses no-flash partial refresh normally, full refresh every N cycles
void smartDisplay(const uint8_t *image);

// Show mode name preview screen (displayed briefly on double-click before loading)
void showModePreview(const char *modeName);

// Draw mixed CJK + ASCII text. 16x16 CJK cell; ASCII is scaled (default 2 -> 10x14)
// and bottom-aligned within the cell. Falls back to a hollow box for missing CJK.
void drawMixed(int x, int y, const char *text, int asciiScale = 2);

// Pixel width of text drawn by drawMixed (must use the same asciiScale).
int measureMixed(const char *text, int asciiScale = 2);

// Native todo item — body text is rendered on-device via drawMixed.
struct TodoItem {
    const char *text;    // body text (UTF-8, CJK + ASCII mixed)
    bool done;           // checked state
    const char *remind;  // reminder time, ASCII like "14:00"; NULL = none
};

// Render the full native todo screen: status bar + todo rows + footer.
void renderTodoScreen(const TodoItem *items, int count, int page, int totalPages, int batteryPct);

#endif // INKSIGHT_DISPLAY_H
