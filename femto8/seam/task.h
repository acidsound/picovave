/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam: task/time slice of FreeRTOS used by femto8. */
#pragma once
#include "FreeRTOS.h"

TickType_t xTaskGetTickCount(void);
void       vTaskDelay(TickType_t ticks);
