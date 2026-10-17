/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam: the slice of FreeRTOS that femto8's __DA1470x__ branch uses.
 * The implementation is per-target (device: busy delay + IRQ-off critical section;
 * simulator: no-ops, the page owns the clock). */
#pragma once
#include <stdint.h>
#include <stddef.h>

typedef uint32_t TickType_t;
typedef long     BaseType_t;
typedef unsigned long UBaseType_t;

#define pdTRUE  1
#define pdFALSE 0
#define pdPASS  1
#define pdFAIL  0

#define portMAX_DELAY       0xFFFFFFFFu
#define portTICK_PERIOD_MS  1
#define configTICK_RATE_HZ  1000
#define pdMS_TO_TICKS(x)    ((TickType_t)(x))

typedef void *SemaphoreHandle_t;
typedef void *TaskHandle_t;

/* The DA1470x SDK's FreeRTOS.h pulls in the whole surface; femto8 relies on that
 * (p8_emu.c includes only FreeRTOS.h + task.h yet calls the semaphore API). */
#include "task.h"
#include "semphr.h"
