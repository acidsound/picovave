/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam: the binary semaphore femto8 uses to hand a finished frame to
 * the display. Single-threaded on both targets, so take/give simply succeed. */
#pragma once
#include "FreeRTOS.h"

SemaphoreHandle_t xSemaphoreCreateBinary(void);
BaseType_t        xSemaphoreGive(SemaphoreHandle_t s);
BaseType_t        xSemaphoreTake(SemaphoreHandle_t s, TickType_t ticks);
